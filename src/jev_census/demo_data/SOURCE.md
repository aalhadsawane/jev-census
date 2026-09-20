# Demo corpus source

`stories.parquet` — Hacker News story titles, retrieved 2026-09-21 via the public
[Hacker News API](https://github.com/HackerNews/API) (`https://hacker-news.firebaseio.com/v0/`), operated by
Y Combinator. The API is free, public, and requires no authentication or API key; its terms state it is
provided for building applications on top of Hacker News data.

**Method:** fetched the `topstories`, `newstories`, `beststories`, `askstories`, and `showstories` id lists
(covering current front-page and recent activity), then padded with a random sample of historical item ids
across the site's full id range (`maxitem.json` at fetch time) to broaden the time coverage. Each id was
resolved via `/v0/item/<id>.json`; entries were kept when `type == "story"` and the title was non-empty.
Comments, jobs, and polls were discarded.

**Columns kept:** `id` (HN item id, used as `--id-field`), `title` (the only free-text field — HN stories
carry no separate body), `url` (the linked page, may be empty for text posts), `by` (submitter username),
`time` (Unix timestamp), `score`, `descendants` (comment count).

**Fields deliberately not requested or stored:** comment text, account emails, or any field beyond what the
public API returns for an item. Usernames (`by`) are already public on Hacker News under the same terms
that make the API itself public.

**Row count and size:** see the file itself — `pq.ParquetFile('stories.parquet').metadata.num_rows`. Kept
under the ~1 MB budget in `06-P7-ADOPTION.md` by storing text-only columns with no engagement beyond title.

**Why Hacker News, again in P7:** `03-LAUNCH.md` already chose Hacker News for the P8 launch analysis, for
the same reasons that make it a good *demo* corpus: public, redistributable, recognisable to a technical
audience, and a genuine semantic-classification target (`grep` cannot answer "does this announce something
the author built") — so the demo doubles as a preview of the launch analysis's shape, not a separate
invented example.

**Regenerating this corpus:** the fetch is a one-time script, not part of the package — see
`DECISIONS.md`'s P7 entry for the exact method if it needs to be re-run (e.g. to refresh with more recent
stories before publication).
