import errno
import os
from pathlib import Path
import shutil
import stat


def fail(error):
    raise error


def copy_directory(source, destination):
    try:
        root = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        if error.errno in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
            return
        raise
    try:
        for path, directories, files, directory in os.fwalk('.', dir_fd=root, follow_symlinks=False, onerror=fail):
            directories[:] = [name for name in directories if not name.startswith('.')]
            for name in files:
                if name.startswith('.'):
                    continue
                try:
                    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                except OSError as error:
                    if error.errno == errno.ELOOP:
                        continue
                    raise
                with os.fdopen(descriptor, 'rb') as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        continue
                    target = destination / path / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open('wb') as output:
                        shutil.copyfileobj(stream, output)
                    target.chmod(0o644)
    finally:
        os.close(root)


def main():
    target = Path(os.environ['RUNNER_TEMP']) / 'gate-artifacts'
    if target.exists():
        shutil.rmtree(target)
    target.mkdir()
    copy_directory('reports', target / 'reports')
    if os.environ.get('PACKAGE_ATTEMPT') == 'true':
        copy_directory('dist', target / 'dist')


if __name__ == '__main__':
    main()
