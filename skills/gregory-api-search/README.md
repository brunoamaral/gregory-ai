# gregory-api-search skill

A portable [Agent Skill](https://docs.claude.com/en/docs/claude-code/skills) that teaches Claude
to query the **GregoryAI research API** at `https://api.brain-regeneration.com` **read-only** —
searching scientific articles and clinical trials, looking up authors, browsing research subjects
and categories, and reading RSS feeds. No API key or login is needed for reads.

## Contents

```
gregory-api-search/
├── SKILL.md                              ← entry point (loaded first)
└── references/
    ├── articles.md                       ← all /articles/ filters + fields
    ├── trials.md                         ← all /trials/ filters + registry-ID lookups
    ├── authors.md                        ← /authors/ lookup
    ├── subjects-and-categories.md        ← topic/category discovery
    ├── search-syntax.md                  ← boolean search= grammar
    └── rss-feeds.md                      ← author & trial RSS feeds
```

## Install / distribute

**Claude Code (personal):** copy this folder into `~/.claude/skills/`:

```bash
cp -r gregory-api-search ~/.claude/skills/
```

**Claude Code (project):** copy it into `.claude/skills/` inside a repo.

**Share it:** zip the folder and hand it to anyone —

```bash
zip -r gregory-api-search.zip gregory-api-search
```

They drop it into one of the locations above. Claude activates it automatically when a request
matches the description in `SKILL.md` (finding papers/trials, searching this corpus, etc.).

## Scope

Read-only. The skill only ever issues `GET` requests and documents no write operations.
