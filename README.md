# Rule1 Real-Time MCP Server

A real-time [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server for Meta Ads, powered by [Rule1](https://rule1.ai). Query the Meta Graph API directly from your AI tools — get live campaign data, performance metrics, creative details, and targeting insights.

Built on top of the excellent [meta-ads-mcp](https://github.com/pipeboard-co/meta-ads-mcp) by the team at [Pipeboard](https://pipeboard.co). We've integrated it with Rule1's auth system and multi-tenant ad account management so that Rule1 users can access their Meta Ads data in real-time through Claude Code, Cursor, and other MCP clients.

> **DISCLAIMER:** This is an unofficial third-party tool and is not associated with, endorsed by, or affiliated with Meta in any way. Meta, Facebook, Instagram, and other Meta brand names are trademarks of their respective owners.

## Getting Started

### Install in Claude Code

```bash
claude mcp add rule1-realtime --transport http https://mcp-rt.rule1.ai/mcp
```

Then restart Claude Code and authenticate:

1. Run `/mcp` and select **rule1-realtime**
2. Click **Authenticate** — sign in with your Rule1 account
3. The server shows as **connected**

### Install in Cursor

Add to your `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "rule1-realtime": {
      "url": "https://mcp-rt.rule1.ai/mcp"
    }
  }
}
```

### Other MCP Clients

Use the endpoint: `https://mcp-rt.rule1.ai/mcp`

Authentication uses OAuth 2.1 (Clerk) — your MCP client will handle the login flow automatically.

## Usage

### Step 1: Set your organization and list accounts

```
get_ad_accounts(organization_id="org_xxx")
```

Use the organization ID from your Rule1 dashboard. This only needs to be called once per session.

### Step 2: Use any tool with your account ID

```
get_campaigns(account_id="act_xxx", limit=10)
get_insights(account_id="act_xxx", time_range="last_7d", level="campaign")
get_ads(account_id="act_xxx", campaign_id="123...")
```

All tools require `account_id`. Use the `platformAccountId` from step 1 with the `act_` prefix.

## Available Tools

### Account & Discovery

| Tool | Description |
|------|-------------|
| `get_ad_accounts` | List connected ad accounts for your organization |
| `get_account_info` | Account details — spend, currency, timezone, DSA status |
| `get_account_pages` | Facebook pages linked to the account |
| `search_pages_by_name` | Find pages by name |
| `get_login_link` | Auth status check |

### Campaigns, Ad Sets & Ads

| Tool | Description |
|------|-------------|
| `get_campaigns` | List campaigns with status, budget, objective |
| `get_campaign_details` | Full details for a single campaign |
| `get_adsets` | List ad sets with targeting and optimization |
| `get_adset_details` | Full details for a single ad set |
| `get_ads` | List ads with creative IDs |
| `get_ad_details` | Full details for a single ad |

### Creatives & Media

| Tool | Description |
|------|-------------|
| `get_ad_creatives` | Creative content — copy, headlines, media |
| `get_creative_details` | Full creative breakdown with asset feed spec |
| `get_ad_image` | Ad image URL and hash |
| `get_ad_video` | Ad video URL, thumbnail, duration |

### Performance & Insights

| Tool | Description |
|------|-------------|
| `get_insights` | Performance metrics — spend, ROAS, conversions, CPA |

### Targeting & Audience

| Tool | Description |
|------|-------------|
| `search_interests` | Search interest targeting options by keyword |
| `get_interest_suggestions` | Get related interest suggestions |
| `estimate_audience_size` | Estimate reach for a targeting spec |
| `search_behaviors` | Browse behavior targeting options |
| `search_demographics` | Browse demographic targeting options |
| `search_geo_locations` | Search geographic targeting locations |

## How It Works

This server connects two systems:

1. **Rule1** ([rule1.ai](https://rule1.ai)) — manages user authentication (Clerk OAuth 2.1), organization/ad account mapping, and encrypted Meta access token storage
2. **Meta Graph API** — the actual ad data source

When you call a tool:
1. Your Clerk OAuth token is sent with the request
2. The server validates it against the Rule1 API
3. Rule1 returns the decrypted Meta access token for your ad account
4. The server calls Meta's Graph API with that token
5. Live data is returned to your AI tool

## Credits

This project is a fork of [meta-ads-mcp](https://github.com/pipeboard-co/meta-ads-mcp) created by [Pipeboard](https://pipeboard.co). The original MCP server provides the full suite of Meta Ads tools — we've adapted the auth layer to work with Rule1's multi-tenant platform while keeping the excellent tool implementations intact.

## Licensing

Licensed under the [Business Source License 1.1](LICENSE):

- Free to use for individual and business purposes
- Modify and customize as needed
- Becomes fully open source (Apache 2.0) on January 1, 2029

## Uninstall

```bash
claude mcp remove rule1-realtime
```
