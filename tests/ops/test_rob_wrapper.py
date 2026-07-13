from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_fake_command(tmp_path: Path, name: str, body: str) -> Path:
    command_path = tmp_path / name
    command_path.write_text(body, encoding="utf-8")
    command_path.chmod(0o755)
    return command_path


def test_rob_wrapper_uses_http_ops_without_python(tmp_path: Path):
    log_path = tmp_path / "curl.log"
    _write_fake_command(
        tmp_path,
        "curl",
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$ROB_TEST_LOG\"\n"
        "printf '{\"ok\":true}\\n200'\n",
    )
    symlink_path = tmp_path / "rob"
    symlink_path.symlink_to(REPO_ROOT / "ops" / "cli" / "rob")

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["ROB_TEST_LOG"] = str(log_path)

    result = subprocess.run(
        [str(symlink_path), "maintenance", "status"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert '{"ok":true}' in result.stdout
    assert "http://127.0.0.1:8811/maintenance" in log_path.read_text(encoding="utf-8")


def test_rob_wrapper_lists_dommes_via_psql_without_python(tmp_path: Path):
    log_path = tmp_path / "psql.log"
    _write_fake_command(
        tmp_path,
        "psql",
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$ROB_TEST_LOG\"\n"
        "printf '123\\tMistress\\tmistress\\tactive\\tactive\\n'\n",
    )
    symlink_path = tmp_path / "rob"
    symlink_path.symlink_to(REPO_ROOT / "ops" / "cli" / "rob")

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["ROB_TEST_LOG"] = str(log_path)
    env["DATABASE_URL"] = "postgresql://runtime/db"

    result = subprocess.run(
        [str(symlink_path), "dommes", "list", "--guild", "42"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert "Dom/me List" in result.stdout
    assert "Total Registered: 1" in result.stdout
    assert "Mistress" in result.stdout
    assert "WHERE guild_id = 42" in log_path.read_text(encoding="utf-8")


def test_robctl_wrapper_resolves_real_repo_root_from_symlink(tmp_path: Path):
    log_path = tmp_path / "curl.log"
    _write_fake_command(
        tmp_path,
        "curl",
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$ROB_TEST_LOG\"\n"
        "printf '{\"ok\":true}\\n200'\n",
    )
    symlink_path = tmp_path / "robctl"
    symlink_path.symlink_to(REPO_ROOT / "ops" / "cli" / "robctl")

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["ROB_TEST_LOG"] = str(log_path)

    result = subprocess.run(
        [str(symlink_path), "block", "123"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert '{"ok":true}' in result.stdout
    assert "/block" in log_path.read_text(encoding="utf-8")


def test_rob_wrapper_no_longer_invokes_ops_ops():
    wrapper = (REPO_ROOT / "ops" / "cli" / "rob").read_text(encoding="utf-8")
    assert "ops.ops" not in wrapper
    assert "PYTHON_BIN" not in wrapper


def test_rob_wrapper_scan_uses_text_scan_endpoint(tmp_path: Path):
    log_path = tmp_path / "curl.log"
    _write_fake_command(
        tmp_path,
        "curl",
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$ROB_TEST_LOG\"\n"
        "printf 'Guild Scan\\n200'\n",
    )
    symlink_path = tmp_path / "rob"
    symlink_path.symlink_to(REPO_ROOT / "ops" / "cli" / "rob")

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["ROB_TEST_LOG"] = str(log_path)

    result = subprocess.run(
        [str(symlink_path), "scan", "--guild", "42"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert "Guild Scan" in result.stdout
    assert "/guilds/42/scan?format=text" in log_path.read_text(encoding="utf-8")


def test_rob_wrapper_auto_apply_posts_selected_options(tmp_path: Path):
    log_path = tmp_path / "curl.log"
    _write_fake_command(
        tmp_path,
        "curl",
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$ROB_TEST_LOG\"\n"
        "printf 'Guild Auto-Apply\\n200'\n",
    )
    symlink_path = tmp_path / "rob"
    symlink_path.symlink_to(REPO_ROOT / "ops" / "cli" / "rob")

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["ROB_TEST_LOG"] = str(log_path)

    result = subprocess.run(
        [str(symlink_path), "auto-apply", "--guild", "42", "channels,domme_role_id"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    log_text = log_path.read_text(encoding="utf-8")
    assert "Guild Auto-Apply" in result.stdout
    assert "/guilds/42/scan/apply?format=text" in log_text
    assert "options=channels,domme_role_id" in log_text


def test_rob_wrapper_send_update_posts_send_update_request(tmp_path: Path):
    log_path = tmp_path / "curl.log"
    _write_fake_command(
        tmp_path,
        "curl",
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$ROB_TEST_LOG\"\n"
        "printf 'Send Approval Requested\\n200'\n",
    )
    symlink_path = tmp_path / "rob"
    symlink_path.symlink_to(REPO_ROOT / "ops" / "cli" / "rob")

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["ROB_TEST_LOG"] = str(log_path)
    env["ROB_ACTOR_NAME"] = "Pat"

    result = subprocess.run(
        [
            str(symlink_path),
            "send",
            "update",
            "missadore",
            "321",
            "18.75",
            "--message",
            "654321",
            "--reason",
            "Price correction",
            "--guild",
            "42",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    log_text = log_path.read_text(encoding="utf-8")
    assert "Send Approval Requested" in result.stdout
    assert "/guilds/42/send-requests/update?format=text" in log_text
    assert "send_id=321" in log_text
    assert "message_id=654321" in log_text
    assert "amount=18.75" in log_text
    assert "reason=Price correction" in log_text


def test_rob_wrapper_migration_audit_uses_text_endpoint(tmp_path: Path):
    log_path = tmp_path / "curl.log"
    _write_fake_command(
        tmp_path,
        "curl",
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$ROB_TEST_LOG\"\n"
        "printf 'Migration Audit\\n200'\n",
    )
    symlink_path = tmp_path / "rob"
    symlink_path.symlink_to(REPO_ROOT / "ops" / "cli" / "rob")

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["ROB_TEST_LOG"] = str(log_path)

    result = subprocess.run(
        [str(symlink_path), "migration", "audit", "--guild", "42"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert "Migration Audit" in result.stdout
    assert "/guilds/42/migration/audit?format=text" in log_path.read_text(encoding="utf-8")


def test_rob_wrapper_webhook_preview_and_send_use_text_endpoints(tmp_path: Path):
    log_path = tmp_path / "curl.log"
    _write_fake_command(
        tmp_path,
        "curl",
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$ROB_TEST_LOG\"\n"
        "printf 'Webhook Reissue Preview\\n200'\n",
    )
    symlink_path = tmp_path / "rob"
    symlink_path.symlink_to(REPO_ROOT / "ops" / "cli" / "rob")

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["ROB_TEST_LOG"] = str(log_path)

    subprocess.run(
        [str(symlink_path), "webhook", "preview", "--guild", "42"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    subprocess.run(
        [str(symlink_path), "webhook", "send", "--guild", "42", "--all", "--limit", "2"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    log_text = log_path.read_text(encoding="utf-8")
    assert "/guilds/42/webhook/reissue/preview?format=text" in log_text
    assert "/guilds/42/webhook/reissue/send?format=text" in log_text
    assert "all=true" in log_text
    assert "limit=2" in log_text


def test_rob_wrapper_webhook_refresh_uses_text_endpoint(tmp_path: Path):
    log_path = tmp_path / "curl.log"
    _write_fake_command(
        tmp_path,
        "curl",
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$ROB_TEST_LOG\"\n"
        "printf 'Webhook URL Refreshed\\n200'\n",
    )
    symlink_path = tmp_path / "rob"
    symlink_path.symlink_to(REPO_ROOT / "ops" / "cli" / "rob")

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["ROB_TEST_LOG"] = str(log_path)

    subprocess.run(
        [str(symlink_path), "webhook", "refresh", "missadore", "--guild", "42"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    log_text = log_path.read_text(encoding="utf-8")
    assert "/guilds/42/webhook/reissue/refresh?format=text" in log_text
    assert "domme_lookup=missadore" in log_text


def test_rob_wrapper_clear_rob_dev_v2_prints_sql_only(tmp_path: Path):
    log_path = tmp_path / "psql.log"
    _write_fake_command(
        tmp_path,
        "psql",
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$ROB_TEST_LOG\"\n"
        "if printf '%s\\n' \"$*\" | grep -q \"COUNT(*) FROM\"; then printf '3\\n'; else printf 'ok\\n'; fi\n",
    )
    symlink_path = tmp_path / "rob"
    symlink_path.symlink_to(REPO_ROOT / "ops" / "cli" / "rob")

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["ROB_TEST_LOG"] = str(log_path)
    env["DATABASE_URL"] = "postgresql://runtime/rob_dev_v2"

    result = subprocess.run(
        [str(symlink_path), "clear", "rob_dev_v2"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert "Rehearsal DB Clear Preview" in result.stdout
    assert "TRUNCATE TABLE" in result.stdout
    assert "db_build_version: preserved" in result.stdout
    assert "COUNT(*) FROM send_change_requests" in log_path.read_text(encoding="utf-8")


def test_rob_wrapper_send_add_uses_pat_actor_alias(tmp_path: Path):
    log_path = tmp_path / "curl.log"
    _write_fake_command(
        tmp_path,
        "curl",
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$ROB_TEST_LOG\"\n"
        "printf 'Send Approval Requested\\n200'\n",
    )
    symlink_path = tmp_path / "rob"
    symlink_path.symlink_to(REPO_ROOT / "ops" / "cli" / "rob")

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["ROB_TEST_LOG"] = str(log_path)
    env["USER"] = "pfaint"

    subprocess.run(
        [str(symlink_path), "send", "add", "missadore", "10.00", "--guild", "42"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert "requested_by=Pat" in log_path.read_text(encoding="utf-8")
