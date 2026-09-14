"""Fault injection inside a disposable installer container, invoked by the Docker smoke."""
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import install


def check(parent, failure):
    parent.mkdir()
    agents = parent / "AGENTS.md"
    agents.write_text("user rule\n")
    agents.chmod(0o600)
    write = install.write_atomic
    interrupted = False
    restoring = False

    def inject(path, data, **kwargs):
        nonlocal interrupted, restoring
        if restoring and path == agents:
            raise OSError("injected restore storage failure")
        write(path, data, **kwargs)
        if failure == "sigint" and path == agents and not interrupted:
            interrupted = True
            os.kill(os.getpid(), signal.SIGINT)
        if failure == "restore" and path == parent / install.MANIFEST_REL:
            restoring = True
            raise OSError("injected final write failure")

    with patch.object(install, "write_atomic", side_effect=inject), contextlib.redirect_stdout(io.StringIO()):
        try:
            install.execute(install.parse_args(["--target", str(parent)]))
        except KeyboardInterrupt:
            assert failure == "sigint"
        except install.InstallError as exc:
            assert failure == "restore" and "rollback incomplete" in str(exc), str(exc)
        else:
            raise AssertionError("fault was not exercised")
    assert agents.exists() and agents.stat().st_mode & 0o777 == 0o600
    assert not (parent / install.MANIFEST_REL).exists()
    if failure == "sigint":
        assert agents.read_text() == "user rule\n"
        assert list(parent.iterdir()) == [agents]
    else:
        backup, = (parent / install.RECOVERY_ROOT_REL).glob("transaction-*")
        assert (backup / "AGENTS.md").read_text() == "user rule\n"
        assert json.loads((backup / "recovery.json").read_text())["target"] == str(parent)


if __name__ == "__main__":
    root = Path(sys.argv[1])
    check(root / "interrupted", "sigint")
    check(root / "restore-failed", "restore")
    print("Docker interruption rollback and retained recovery backup: ok")
