"""Management utilities."""

import os

from fabric.contrib.console import confirm
from fabric.contrib import files
from fabric.api import abort, env, local, settings, task, require, cd
from fabric.operations import _prefix_commands, _prefix_env_vars, sudo, run

# Edit below
env.proj_name = 'project_title'
env.disable_known_hosts = True # always fails for me without this
hosts = {
         'local':['192.168.1.1'],  # Virtual Machine
         'production':['production.domain.name'],
        }
env.user = 'localadmin'
env.local_user = 'mattb'
env.root = '/srv/{0}'.format(hosts['production']) # where the your application root lives
env.requirements = 'requirements.txt' # location of 
env.git_user = 'highway900'
# *should* work if configured above correctly
env.proj_repo = 'git@bitbucket.org:{0}/{1}.git'.format(env.git_user, env.proj_name)
env.proj_root = os.path.join(env.root, env.proj_name)
env.proj_env = os.path.join(env.root, 'env')
env.pip_file = os.path.join(env.proj_root, env.requirements)

@task
def set_host(hostName):
    env.hosts = hosts[hostName]
    if hostName == 'local':
        env.user = env.local_user

@task
def deploy():
    """initialize the repo
    Update source, update pip requirements, syncdb, restart server"""
    setup_libs()
    if not files.exists(env.root):
        build_tree()
    init_environment()
    if not files.exists(env.proj_root):
        clone()
    else:
        update()
    update_reqs()
    syncdb()
    collect_static()
    config()
    restart()

@task
def collect_static():
    """collect the static files for admin
    """
    with cd(env.proj_root):
        ve_run('python manage.py collectstatic -l --noinput --settings={0}.settings.prod'.format(env.proj_name))

@task
def setup_libs():
    """Install required ubuntu libs
    """
    # if env.linux would be ideal UBUNTU/DEBIAN specific
    libs = ['git-core', 'python-dev', 'nginx', 'supervisor', 'python-pip', 'libmysqlclient-dev']
    sudo('apt-get install ' + ' '.join(libs))

@task
def build_tree():
    """Attempt to build the tree for the app
    """
    print "Building directory structure..."
    sudo('mkdir {0}'.format(env.root))
    sudo('chown {0}:{0} {1}'.format(env.user, env.root))
    
@task
def init_environment():
    """Initialise the environment bby installing VirtualEnv
    then create the environment if it does not exist
    """
    with cd(env.root):
        sudo('pip install virtualenv')
        if not files.exists(env.proj_env):
            print "Creating VirtualEnv..."
            run('virtualenv env')

@task
def config():
    """Load configuration scripts"""
    # copy supervisor configs
    if files.exists('/etc/nginx/sites-enabled/default'):
        sudo('rm /etc/nginx/sites-enabled/default')
    with cd(env.proj_root):
        sudo('cp scripts/*.conf /etc/supervisor/conf.d/.')
        sudo('cp scripts/{0} /etc/nginx/sites-available/.'.format(host['production']))
        sudo('ln -s /etc/nginx/sites-available/{0} /etc/nginx/sites-enabled/default'.format(host['production'])

@task
def switch_branch(branch):
    """Switch the repo branch which the server is using"""
    with cd(env.proj_root):
        ve_run('git checkout %s' % branch)
    restart()

@task
def version():
    """Show last commit to repo on server"""
    with cd(env.proj_root):
        sshagent_run('git log -1')

@task
def restart_gunicorn():
    """Restart Gunicorn"""
    sudo('supervisorctl restart gunicorn')
    run('touch {0}/wsgi.py'.format(env.proj_root))

def restart():
    """Bounce services
    """
    restart_gunicorn()
    restart_rabbitmq()
    restart_celery()

@task
def restart_celery():
    """Restart celeryd"""
    sudo("supervisorctl restart celeryd")
    
@task
def restart_rabbitmq():
    """Restart rabbitmq"""
    sudo("service rabbitmq-server restart")

@task
def reload_nginx():
    """restart the web server"""
    sudo('service nginx reload')

@task
def restart():
    """Restart the queue, gunicorn, nginx"""
    reload_nginx()
    restart_gunicorn()

@task
def update_reqs():
    """Update pip requirements"""
    ve_run('yes w | pip install -r %s' % env.pip_file)

@task
def update():
    """Updates project source"""
    with cd(env.proj_root):
        sshagent_run('git pull')
        sshagent_run('git submodule update')

@task
def clone():
    """Clone the repository for the first time"""
    with cd(env.root):
        sshagent_run('git clone --recursive %s' % env.proj_repo)

@task
def ve_run(cmd):
    """Helper function.
    Runs a command using the virtualenv environment"""

    require('root')
    return sshagent_run('source %s/bin/activate; %s' % (env.proj_env, cmd))

@task
def sshagent_run(cmd):
    """Helper function.
    Runs a command with SSH agent forwarding enabled.

    Note:: Fabric (and paramiko) can't forward your SSH agent.
    This helper uses your system's ssh to do so."""
    
    # Handle context manager modifications
    wrapped_cmd = _prefix_commands(_prefix_env_vars(cmd), 'remote')
    try:
        host, port = env.host_string.split(':')
        return local(
            "ssh -p %s -A %s@%s '%s'" % (port, env.user, host, wrapped_cmd)
        )
    except ValueError:
        return local(
            "ssh -A %s@%s '%s'" % (env.user, env.host_string, wrapped_cmd)
        )

########## HELPERS
def cont(cmd, message):
    """Given a command, ``cmd``, and a message, ``message``, allow a user to
    either continue or break execution if errors occur while executing ``cmd``.

    :param str cmd: The command to execute on the local system.
    :param str message: The message to display to the user on failure.

    .. note::
        ``message`` should be phrased in the form of a question, as if ``cmd``'s
        execution fails, we'll ask the user to press 'y' or 'n' to continue or
        cancel exeuction, respectively.

    Usage::

        cont('heroku run ...', "Couldn't complete %s. Continue anyway?" % cmd)
    """
    with settings(warn_only=True):
        result = local(cmd, capture=True)

    if message and result.failed and not confirm(message):
        abort('Stopped execution per user request.')
########## END HELPERS


########## DATABASE MANAGEMENT
@task
def syncdb():
    """Run syncdb (along with any pending south migrations)"""
    with cd(env.proj_root):
        ve_run('python manage.py syncdb --settings={0}.settings.prod --migrate --noinput'.format(env.proj_name))


@task
def migrate(app=None):
    """Apply one (or more) migrations. If no app is specified, fabric will
    attempt to run a site-wide migration.

    :param str app: Django app name to migrate.
    """
    if app:
        local('%s migrate %s --noinput' % (env.run, app))
    else:
        local('%(run)s migrate --noinput' % env)
########## END DATABASE MANAGEMENT


########## FILE MANAGEMENT
@task
def collectstatic():
    """Collect all static files, and copy them to S3 for production usage."""
    local('%(run)s collectstatic --noinput' % env)
########## END FILE MANAGEMENT
