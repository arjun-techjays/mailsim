"""Integrator wiring: `python -m mailsim.serve protocols`."""

from __future__ import annotations

import pytest

from mailsim import serve as server

pytestmark = pytest.mark.db


def _env(db_engine, tmp_path, **extra: str) -> dict[str, str]:
    url = db_engine.url.render_as_string(hide_password=False)
    return {"MAILSIM_DATABASE_URL": url, "MAILSIM_BLOB_DIR": str(tmp_path), **extra}


def _args(*argv: str):
    return server._parser().parse_args(["protocols", *argv])


def test_protocols_defaults_and_self_signed_tls(db_engine, tmp_path):
    seen = {}

    def run(engine, host, **kw):
        seen.update(kw, host=host, engine=engine)

    env = _env(db_engine, tmp_path, MAILSIM_LOCAL_NO_AUTH="1")
    assert server.protocols_command(_args(), run=run, environ=env) == 0
    assert (seen["host"], seen["imap_port"], seen["smtp_port"]) == ("127.0.0.1", 1143, 1587)
    assert seen["tls"] is False and seen["tls_context"] is None
    assert seen["allow_insecure_remote"] is False
    assert server.protocols_command(_args("--tls-self-signed", "--imap-port", "1"), run=run,
                                    environ=env) == 0  # fmt: skip
    assert seen["tls"] is True and seen["tls_context"] is not None and seen["imap_port"] == 1


def test_protocols_with_certificate_files(db_engine, tmp_path):
    from mailsim.protocols import tls

    cert, key = tls.self_signed_pem()
    (tmp_path / "c.pem").write_bytes(cert)
    (tmp_path / "k.pem").write_bytes(key)
    seen = {}
    args = _args("--tls-cert", str(tmp_path / "c.pem"), "--tls-key", str(tmp_path / "k.pem"))
    assert server.protocols_command(args, run=lambda e, h, **kw: seen.update(kw),
                                    environ=_env(db_engine, tmp_path)) == 0  # fmt: skip
    assert seen["tls"] is True


def test_protocols_refuses_insecure_remote_bind(db_engine, tmp_path, capsys):
    from mailsim.protocols import server as protocols

    def run(engine, host, **kw):  # the real constructor check, without binding sockets
        protocols.ProtocolServers(engine, host=host, imap_port=kw["imap_port"],
                                  smtp_port=kw["smtp_port"], tls=kw["tls"],
                                  tls_context=kw["tls_context"],
                                  allow_insecure_remote=kw["allow_insecure_remote"])  # fmt: skip

    env = _env(db_engine, tmp_path)
    assert server.protocols_command(_args("--host", "0.0.0.0"), run=run, environ=env) == 2  # noqa: S104
    assert "protocols:" in capsys.readouterr().err
    assert server.protocols_command(_args("--host", "0.0.0.0", "--allow-insecure-remote"),  # noqa: S104
                                    run=run, environ=env) == 0  # fmt: skip


def test_protocols_subcommand_is_routed(monkeypatch):
    monkeypatch.setattr(server, "protocols_command", lambda args: 5)
    assert server.main(["protocols"]) == 5


# ---- `serve` starts the listeners beside the API ----------------------------------------------
def _app(env):
    from mailsim.api.app import create_app

    return create_app(engine=env.engine, session_factory=env.session_factory, blobs=env.blobs)


def test_serve_without_protocols_leaves_them_off(env):
    app = _app(env)
    with server.protocol_listeners(app, None) as servers:
        assert servers is None and app.state.protocols is None


def test_serve_runs_the_listeners_for_the_lifetime_of_the_block(env, capsys):
    app = _app(env)
    opts = server.ProtocolOptions(imap_port=0, smtp_port=0, tls_self_signed=True)
    with server.protocol_listeners(app, opts) as servers:
        assert app.state.protocols is servers and servers.running and servers.tls
        assert f"IMAP on 127.0.0.1:{servers.imap_port}" in capsys.readouterr().err
    assert not servers.running


def test_serve_keeps_the_api_when_a_port_is_taken(env, capsys):
    from mailsim.protocols.server import ProtocolServers

    with ProtocolServers(env.engine, imap_port=0, smtp_port=0) as busy:
        for opts in (server.ProtocolOptions(imap_port=busy.imap_port, smtp_port=0),
                     server.ProtocolOptions(imap_port=0, smtp_port=busy.smtp_port)):  # fmt: skip
            app = _app(env)
            with server.protocol_listeners(app, opts) as servers:
                assert app.state.protocols is servers and not servers.running
                assert servers._executor is None  # nothing left behind
            err = capsys.readouterr().err
            assert "IMAP/SMTP listeners not started" in err and "--no-protocols" in err


def test_serve_refuses_insecure_remote_listeners_but_serves_the_api(env, tmp_path, capsys):
    app = _app(env)
    with server.protocol_listeners(app, server.ProtocolOptions(host="192.0.2.1")) as servers:
        assert servers is None and app.state.protocols is None
    assert "refusing to serve IMAP/SMTP on non-loopback" in capsys.readouterr().err
    missing = server.ProtocolOptions(imap_port=0, smtp_port=0, tls_cert=tmp_path / "none.pem")
    with server.protocol_listeners(app, missing) as servers:
        assert servers is None
    assert "IMAP/SMTP listeners not started" in capsys.readouterr().err
