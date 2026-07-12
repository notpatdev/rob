from pathlib import Path


def test_deploy_workflow_rebuild():
    workflow = Path('.github/workflows/deploy-codebase.yml')
    assert workflow.exists()
    text = workflow.read_text()
    assert 'resolve_deploy_context:' in text
    assert 'precheck_codebase:' in text
    assert 'bot_server_precheck:' in text
    assert 'webhook_server_precheck:' in text
    assert 'deploy_bot:' in text
    assert 'deploy_webhook:' in text
    assert 'push:' in text and 'main' in text
    assert 'workflow_dispatch:' in text
    assert 'needs: [resolve_deploy_context, precheck_codebase, bot_server_precheck]' in text
    assert 'needs: [resolve_deploy_context, precheck_codebase, webhook_server_precheck]' in text
    assert 'appleboy/ssh-action' in text
    assert "if: ${{ needs.resolve_deploy_context.outputs.deploy_bot == 'true' }}" in text
    assert "if: ${{ needs.resolve_deploy_context.outputs.deploy_webhook == 'true' }}" in text
    assert 'precheck-bot.sh is not present yet. Running bootstrap-safe fallback checks.' in text
    assert 'precheck-webhook.sh is not present yet. Running bootstrap-safe fallback checks.' in text
    assert 'Skipping DB schema check in bootstrap fallback. Full DB check runs in deploy-bot.sh.' in text
    assert 'Skipping DB schema check in bootstrap fallback. Full DB check runs in deploy-webhook.sh.' in text
    for secret in [
    'ROB_PROD_BOT_HOST',
    'ROB_PROD_BOT_USER',
    'ROB_PROD_BOT_SSH_KEY',
    'ROB_PROD_BOT_SSH_PORT',
    'ROB_PROD_WEBHOOK_HOST',
    'ROB_PROD_WEBHOOK_USER',
    'ROB_PROD_WEBHOOK_SSH_KEY',
    'ROB_PROD_WEBHOOK_SSH_PORT',
    ]:
        assert secret in text
    assert 'scripts/run_migrations.py' not in text
    assert 'db/build/001_core_schema.sql' not in text


def test_deploy_scripts_and_docs():
    pre_bot = Path('deploy/scripts/precheck-bot.sh').read_text()
    pre_webhook = Path('deploy/scripts/precheck-webhook.sh').read_text()
    check_bot = Path('deploy/scripts/check-bot-runtime.sh').read_text()
    check_webhook = Path('deploy/scripts/check-webhook-runtime.sh').read_text()
    check_bot_py = Path('ops/checks/check_bot_runtime.py').read_text()
    check_webhook_py = Path('ops/checks/check_webhook_runtime.py').read_text()
    deploy_bot = Path('deploy/scripts/deploy-bot.sh').read_text()
    deploy_webhook = Path('deploy/scripts/deploy-webhook.sh').read_text()
    bot_dev = Path('deploy/scripts/deploy-bot-dev.sh').read_text()
    webhook_dev = Path('deploy/scripts/deploy-webhook-dev.sh').read_text()
    docs = Path('docs/deployment.md').read_text()
    cloudflared_script = Path('deploy/scripts/install-cloudflared-webhook.sh')
    cloudflared_doc = Path('docs/cloudflared-webhook.md')
    prod_install_bot = Path('deploy/scripts/install-bot.sh')
    prod_install_webhook = Path('deploy/scripts/install-webhook.sh')
    prod_bot_service = Path('deploy/systemd/rob-bot.service')
    prod_webhook_service = Path('deploy/systemd/rob-webhook.service')

    assert 'ops/checks/check_db.py' in pre_bot and 'ops/checks/check_db.py' in pre_webhook
    assert 'ops/checks/check_db.py' in check_bot and 'ops/checks/check_db.py' in check_webhook
    assert 'systemctl restart' not in pre_bot and 'systemctl restart' not in pre_webhook
    assert 'systemctl restart' not in check_bot and 'systemctl restart' not in check_webhook
    assert 'echo .env' not in pre_bot and 'echo .env' not in pre_webhook
    assert 'source .env' not in pre_bot and 'source .env' not in pre_webhook
    assert 'source .env' not in check_bot and 'source .env' not in check_webhook
    assert 'load_env_file ".env"' in pre_bot and 'load_env_file ".env"' in pre_webhook
    assert 'load_env_file ".env"' in check_bot and 'load_env_file ".env"' in check_webhook
    assert 'Invalid .env syntax on line' in pre_bot and 'Invalid .env syntax on line' in pre_webhook
    assert 'ops/checks/check_bot_runtime.py' in check_bot
    assert 'ops/checks/check_webhook_runtime.py' in check_webhook
    assert 'load_bot_settings' in check_bot_py
    assert 'load_webhook_settings' in check_webhook_py
    assert 'notify_url=' in check_bot_py
    assert 'public_base=' in check_webhook_py
    assert 'ROB_BOT_NOTIFY_URL' in check_webhook
    assert 'exec "${SCRIPT_DIR}/deploy-bot.sh" "$@"' in bot_dev
    assert 'exec "${SCRIPT_DIR}/deploy-webhook.sh" "$@"' in webhook_dev
    assert 'db/build/001_core_schema.sql' in deploy_bot
    assert 'db/build/002_indexes.sql' in deploy_bot
    assert 'SQLite data migration remains separate' in docs
    assert 'Do not expose PostgreSQL publicly' in docs
    assert cloudflared_script.exists()
    assert cloudflared_doc.exists()
    assert prod_install_bot.exists()
    assert prod_install_webhook.exists()
    assert prod_bot_service.exists()
    assert prod_webhook_service.exists()

    assert deploy_bot
    assert deploy_webhook


def test_cloudflared_webhook_installer_guards():
    script_path = Path('deploy/scripts/install-cloudflared-webhook.sh')
    assert script_path.exists()
    script = script_path.read_text()

    assert 'throne.robthebot.com' in script
    assert 'http://127.0.0.1:8080' in script
    assert 'TUNNEL_TOKEN=' not in script
    assert 'eyJh' not in script
    assert 'ufw allow 8080' not in script
    assert 'firewall-cmd' not in script
    assert 'iptables' not in script
    assert '.bak-$(date +%Y%m%d-%H%M%S)' in script
    assert 'cp -a "${CLOUDFLARED_CONFIG}" "${backup_path}"' in script
    assert 'cloudflared_run tunnel login' in script
    assert 'cloudflared_run tunnel create "${TUNNEL_NAME}"' in script
    assert 'cloudflared_run tunnel route dns "${TUNNEL_NAME}" "${PUBLIC_HOSTNAME}"' in script
    assert 'token-managed' not in script



def test_prod_installers_and_manual_setup_target_real_prod():
    bot_script = Path('deploy/scripts/install-bot.sh').read_text()
    webhook_script = Path('deploy/scripts/install-webhook.sh').read_text()
    setup_sql = Path('db/manual/setup_rob_prod.sql').read_text()

    assert 'rob-bot.service' in bot_script
    assert 'rob-webhook.service' in webhook_script
    assert 'rob_prod' in bot_script
    assert 'rob_prod' in webhook_script
    assert 'patdadev/rob.git' in bot_script
    assert 'patdadev/rob.git' in webhook_script
    assert 'CREATE ROLE prod_rob_bot LOGIN' in setup_sql
    assert 'CREATE ROLE prod_rob_webhook LOGIN' in setup_sql
    assert 'CREATE DATABASE rob_prod OWNER doadmin' in setup_sql
    assert '\\ir ../build/001_core_schema.sql' in setup_sql
    assert '\\ir ../build/007_send_update_requests.sql' in setup_sql
    assert '\\ir ../build/008_dm_preferences.sql' in setup_sql
    assert '\\ir ../build/009_terms_acceptance.sql' in setup_sql
    assert '\\ir ../grants/prod_rob_bot.sql' in setup_sql
    assert '\\ir ../grants/prod_rob_webhook.sql' in setup_sql
