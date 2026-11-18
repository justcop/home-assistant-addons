# Listening Analytics setup

1. Open **Configuration** and enter your Last.fm `username` and `api_key`.
2. Save, restart and select **Open Web UI**.
3. Leave the add-on running while it imports your history. Progress is visible, and completed windows survive restarts.

[Create a Last.fm API key](https://www.last.fm/api/account/create). Only the API key is needed, not your API secret or password.

The default sync interval is five minutes. The timezone defaults to Europe/London and includes daylight-saving changes. `reconcile_days` controls how much recent history is checked daily for edits and deletions. Save and restart after changing options.

Use **Overview** for totals and highlights, **Trends** for patterns, and **Artists**, **Albums**, **Songs** or **History** to explore. Select a chart bar to inspect the plays behind it. Choose **Combined versions** or **Last.fm entries** to compare totals.

In **Settings & grouping**, select song or album groups by the same artist and choose **Merge selected**. Open a group to inspect its versions and separate one. **Undo latest change** reverses a manual decision. These changes are local; Last.fm is never edited.

The fictional demo is available from the setup page, or by enabling `demo_mode`. It uses a separate database and never contacts Last.fm. Disable it and restart to return to your own account.

Your persistent database is in `/data`. Include this add-on in Home Assistant backups. Changing username opens a separate account database. Access uses Home Assistant Ingress by default. For a standalone phone app, set `web_password` (at least 12 characters), restart, enable the optional `8099/tcp` host port in Network and route an HTTPS reverse proxy to it. Only the HTTPS address should be public. Direct login cookies require HTTPS. Leave the password blank to block direct access.

If sync reports an error, check your username, API key and connectivity. Temporary failures retry automatically. A restart retries an interrupted import from its last completed window. Discovery and percentage comparisons wait for the full initial import so partial data does not create misleading figures.

See [the full README](https://github.com/justcop/home-assistant-addons/blob/main/lastfm_analytics/README.md) for the import model, grouping rules, data limitations and development instructions.


## Links between Listening Analytics and AudioShelf

Update both add-ons. Set `audioshelf_url` in Listening Analytics to the HTTPS AudioShelf address, for example `https://audioshelf.justcop.co.uk`. In AudioShelf, set `listening_analytics_origin` to the origin of the Listening Analytics address (scheme and hostname, with port if needed, without a path). Sign in to AudioShelf in the same browser using the account whose shelf you want to browse.

“On your shelf” appears only after an authenticated check finds matching owned records. Artist links open the full shelf and scroll to that artist. Album links open the saved album page. Song links use saved tracklists, preferring the album from listening history. Multiple matching albums show separate links. “Find in record store” remains available regardless of ownership and opens a search. No credentials or shelf data are copied to Listening Analytics.

The browser must permit the AudioShelf session cookie for the check. Using HTTPS subdomains of the same domain works with the existing cookie settings. Across unrelated domains, browser cookie restrictions may prevent shelf checks; record store links still work. In Home Assistant ingress, use the origin of the Home Assistant page as `listening_analytics_origin`.


## Live gig history and setlist.fm

The **Live gigs** tab stores the concerts and festival performances you attended
independently of your listening history. Import a UTF-8 CSV or JSON file from
that tab, with one row per **artist performance**. CSV headings:

- Required: `artist`, `date` (YYYY-MM-DD or DD-MM-YYYY).
- Optional: `venue`, `location`, `type` (`festival` or `concert`),
  `festival`, `setlist_url` or `setlist_id`, `notes`.

An example CSV:

```csv
artist,date,venue,location,type,festival,setlist_url
Example Band,2025-06-15,Example Arena,Manchester,concert,,
Example Singer,2025-06-28,Pyramid Stage,Somerset,festival,Glastonbury,
```

Imports are idempotent by setlist ID or gig identity; importing the same file
again updates records without multiplying performances.

Set these **Home Assistant add-on Configuration** fields:

| Field | Default | Meaning |
| --- | --- | --- |
| `setlist_fm_api_key` | empty | Personal non-commercial setlist.fm API key; leave empty to disable the worker |
| `setlist_cache_retention_days` | 30 | Delete API-derived setlist payloads after this many days (1–30) |
| `setlist_daily_request_budget` | 10 | Maximum automatic requests per UTC day (1–100) |

All setlists remain **unverified**. The refresh cycle is anchored to the
performance date: daily during the first week, weekly for the remainder of
the first month, monthly for the remainder of the first year, and then once
per year on the concert anniversary. 29 February gigs use 28 February in
non-leap years. If the API budget is exhausted or the add-on is offline,
missed checks remain due and are processed in priority order. Delays never
change the underlying anniversary.

Dates, artists and venues supplied in the import are yours and remain saved.
Third-party API responses (including song lists) are kept separately in an
expiring cache. A shorter retention option also deletes older existing cache
entries at next startup/cleanup. Retention settings do not change the setlist.fm
API's terms: use only a short caching interval permitted by the provider.

Where no explicit setlist ID is available, the worker searches by exact artist
and date. It **does not** auto-approve multiple candidate matches; add the
correct setlist.fm URL to your import and reimport to resolve ambiguity.

On **artist details**, the listening timeline includes amber concert circles
and purple festival triangles. Events are linked to your imported attendance,
not inferred from a festival's entire lineup. Select a timeline bar to
inspect listening activity and use the event links to open setlist.fm.
