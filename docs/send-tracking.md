# Send tracking

## Server setup

An administrator with **Manage Server** runs:

```text
/bill setup
```

The command posts a public, initiator-only setup flow. Bill checks **View
Channel**, **Send Messages**, **Embed Links**, and **Read Message History**
before saving the selected channel.

## Dom/me setup

Dom/me and switch members connect Throne privately while creating or editing
their profile:

```text
/profile
```

A full `https://throne.com/...` or `https://throne.gifts/...` profile URL also
works. Bill resolves the public creator, links it to the Discord user, and
returns a private webhook URL with installation steps.

Webhook URLs use `https://usebill.dev/t/<creator>/<secret>`. Authenticated bot
API routes remain under `https://usebill.dev/v1`.

The webhook URL contains a secret. Do not post or share it. Bill stores only its
SHA-256 hash. If the URL is exposed, use the profile editor's explicit rotation
action; the previous URL stops working.

The same Discord user can link that Throne creator in more than one server. The
existing webhook remains valid and each verified send is posted to every
configured server.

## Event handling

Bill accepts supported Throne purchase, contribution, and crowdfunding events.
Amounts are stored in the event currency as integer minor units. Currency
conversion is not part of this milestone.

- Test/setup events verify the webhook but do not create a send.
- Unsupported events are acknowledged and ignored.
- Private sends hide the amount and sender.
- Anonymous sends keep permitted amount/item details but do not keep the sender
  identity.
- Non-private, non-anonymous future sends may be attributed when the sender
  name unambiguously matches one effective profile alias in that guild.
- Repeated delivery of the same Throne event creates no duplicate Discord post.

If a channel is deleted or Bill loses access, an administrator should restore
the required permissions or rerun `/bill setup` with a working channel.
