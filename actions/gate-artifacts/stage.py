import errno
import os
from pathlib import Path
import shutil
import signal
import stat


RESERVE = 1024 ** 3


def fail(error):
    raise error


def regular_files(source):
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
                    if stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        yield Path(path) / name, stream
    finally:
        os.close(root)


def require_space(target, size, summary):
    free = shutil.disk_usage(target).free
    if size > free - RESERVE:
        print(f'::warning::Skipping Test Gate artifact staging: {summary}; '
              f'{free} bytes free, preserving {RESERVE} bytes for JUnit, teardown and logs', flush=True)
        raise OSError(errno.ENOSPC, 'insufficient artifact staging space')


def interrupted(signum, frame):
    raise InterruptedError('artifact staging interrupted')


def main():
    signal.signal(signal.SIGTERM, interrupted)
    target = Path(os.environ['RUNNER_TEMP']) / 'gate-artifacts'
    if target.exists():
        shutil.rmtree(target)
    target.mkdir()
    try:
        sources = ['reports', 'dist'] if os.environ.get('PACKAGE_ATTEMPT') == 'true' else ['reports']
        sizes = {source: sum(os.fstat(stream.fileno()).st_size for _, stream in regular_files(source))
                 for source in sources}
        summary = ', '.join(f'{source}/ ({size} bytes)' for source, size in sizes.items())
        if os.environ.get('GITHUB_ENV'):
            with open(os.environ['GITHUB_ENV'], 'a') as environment:
                environment.write(f'GATE_ARTIFACT_SIZES={summary}\n')
        if sum(sizes.values()):
            print(f'Artifact sources: {summary}', flush=True)
            require_space(target, sum(sizes.values()), summary)
        for source in sources:
            for path, stream in regular_files(source):
                require_space(target, os.fstat(stream.fileno()).st_size, summary)
                destination = target / source / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open('wb') as output:
                    shutil.copyfileobj(stream, output)
                destination.chmod(0o644)
    except BaseException:
        shutil.rmtree(target)
        raise


if __name__ == '__main__':
    main()
