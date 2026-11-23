"""One-shot container initialization. The NAS source mounts are read-only."""
import os
import sys
from pathlib import Path


PROGRAM_FILES = ('app.py', 'web/index.html', 'web/style.css', 'web/app.js')


def stage_program(source, program, data, chown=None):
    if chown is None:
        chown = os.chown
    source, program, data = Path(source), Path(program), Path(data)
    # Read all required sources before touching the running program or data volume.
    payloads = {name: (source / name).read_bytes() for name in PROGRAM_FILES}
    if any(not payload for payload in payloads.values()):
        raise ValueError('项目文件不完整：app.py 或 web 文件为空，请重新解压完整部署包')
    program.mkdir(parents=True, exist_ok=True)
    (program / 'web').mkdir(exist_ok=True)
    for directory in (program, program / 'web'):
        os.chmod(directory, 0o755)
        chown(directory, 10001, 10001)
    for name, payload in payloads.items():
        destination = program / name
        temporary = destination.with_name(destination.name + '.new')
        temporary.write_bytes(payload)
        os.chmod(temporary, 0o644)
        chown(temporary, 10001, 10001)
        temporary.replace(destination)
    data.mkdir(parents=True, exist_ok=True)
    # Do not recurse through existing settings/cache or change NAS source permissions.
    chown(data, 10001, 10001)
    print('映桥初始化完成：4 个程序文件已就绪，主服务将使用非 root 用户启动', flush=True)


if __name__ == '__main__':
    if '--run' in sys.argv:
        stage_program('/source', '/app', '/data')
        health_script = Path('/app/bootstrap.py')
        temporary = health_script.with_suffix('.new')
        temporary.write_bytes(Path(__file__).read_bytes())
        os.chmod(temporary, 0o644)
        temporary.replace(health_script)
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
        os.execv(sys.executable, [sys.executable, '/app/app.py'])
    elif '--health' in sys.argv:
        from urllib.request import urlopen
        host = os.environ.get('HOST', '0.0.0.0')
        if host == '0.0.0.0':
            host = '127.0.0.1'
        port = int(os.environ.get('PORT', '8080'))
        with urlopen(f'http://{host}:{port}/health', timeout=3):
            pass
    else:
        stage_program('/source', '/program', '/data')
