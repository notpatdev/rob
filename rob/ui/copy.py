from __future__ import annotations

SUCCESS_FOOTER = "Rob kept the paperwork tidy."
ERROR_FOOTER = "In Pat we *somewhat* trust."
WEBHOOK_ERROR_FOOTER = "Pris here."
PERMISSION_ERROR_FOOTER = "Rob is helpful, unfortunately."
DM_FAILED_FOOTER = "Rob brought the envelope. Discord ate it."
SETUP_RENDER_ERROR_FOOTER = "The paperwork fought back."
MAINTENANCE_FOOTER = "Please do not tap the glass."
OFFLINE_FOOTER = "Powered by vibes and consequences."
LEADERBOARD_FOOTER = "Powered by vibes and PostgreSQL."
STATUS_FOOTER = "Professional enough to deploy, opinionated enough to be Rob."
COUNTING_FOOTER = "Rob handled the maths. Somehow."
PERMISSION_ROLE_NOT_CONFIGURED = (
    "Rob checked the little permission clipboard, but this server has not configured the required role yet."
)
PERMISSION_ROLE_MISSING = (
    "Rob checked the little permission clipboard and your name was not on it."
)
PERMISSION_LEADERBOARD_ROLE_MISSING = (
    "The leaderboard is members-only and your name wasn't on Rob's list. "
    "Opt in with `/preferences` (or during Dom/me setup) to get the leaderboard "
    "access role, then try again."
)

DOMME_REGISTERED_TITLE = "You're registered!"
DOMME_REGISTERED_BODY = (
    "Thanks for entrusting Rob with tracking your Throne sends!\n\n"
    "Before we can fully start, there’s just one more thing I need you to do. "
    "In order for Rob to correctly receive your Throne sends, you’ll need to pop a special URL into Throne.\n\n"
    "Because that link is secret, I’ve sent you a DM to help get it sorted."
)

THRONE_SETUP_TITLE = "Throne Tracking Setup!"
WEBHOOK_REISSUE_TITLE = "Action Needed | Rob Upgrade"
WEBHOOK_REFRESH_TITLE = "Rob | New Throne Tracking URL"


def throne_setup_steps(webhook_url: str) -> str:
    return (
        "As my telekinesis skills are a little rusty, we just need to do one final step to help make sure I get told when sends come through. Here's how:\n\n"
        "1. Head to https://throne.com/ and sign in.\n"
        "2. Go to Settings, then click Integrations.\n"
        "3. Scroll until you see Webhooks.\n"
        "4. Click Enable Webhooks.\n"
        "5. Under Subscriber URLs, click Add URL.\n"
        "6. Enter the almighty link below.\n"
        "7. Click Save Settings, then click Test Webhook and wait for the success message.\n\n"
        "Once done, pop back here to see if Rob picked up your send. I'll update this message if it worked.\n\n"
        f"The almighty link:\n```\n{webhook_url}\n```"
    )


def webhook_refresh_message(webhook_url: str) -> str:
    return (
        "Hey!\n\n"
        "It looks like you've requested a new url for automatic throne tracking. Here it is:\n\n"
        f"```{webhook_url}```\n\n"
        "Once you've entered the new link, click Save Settings and then Test Webhook to make sure it works.\n\n"
        "Thanks\n"
        "Pat\n\n"
        "-# This is automated. No need to respond.\n\n"
        "When Throne confirms it worked, press **Yes** below."
    )


def webhook_upgrade_message(*, throne_name: str, webhook_url: str) -> str:
    return (
        f"Hey {throne_name},\n\n"
        "You're marked as one of the awesome people who use the Automatic Throne Tracking provided by Rob. "
        "As part of the Rob upgrade, the links used for this tracking have been changed.\n\n"
        "Your old link looking like `https://rob.barecoding.com` will now be changed to:\n\n"
        f"`{webhook_url}`\n\n"
        "Once done, click Save Settings and Test Webhook.\n\n"
        "---\n"
        "-# This is automated! Have a spectacular day!\n\n"
        "When Throne confirms it worked, press **Yes** below so Rob can finish the reconnect."
    )
