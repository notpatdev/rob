# Send tracking

## Server setup

An administrator with **Manage Server** runs:

```text
/bill setup send_channel:#sends
```

Bill checks **View Channel**, **Send Messages**, and **Embed Links** before
saving the channel.

## Dom/me setup

In a configured server, the Dom/me runs:

```text
/register domme throne:your-throne-name
```

A full `https://throne.com/...` or `https://throne.gifts/...` profile URL also
works. Bill resolves the public creator, links it to the Discord user, and
returns a private webhook URL with installation steps.

Webhook URLs use `https://usebill.dev/t/<creator>/<secret>`. Authenticated bot
API routes remain under `https://usebill.dev/v1`.

The webhook URL contains a secret. Do not post or share it. Bill stores only its
SHA-256 hash. If the URL is exposed, rerun the command with
`reset_webhook:True`; the previous URL stops working.

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
- Repeated delivery of the same Throne event creates no duplicate Discord post.

If a channel is deleted or Bill loses access, an administrator should restore
the required permissions or rerun `/bill setup` with a working channel.
