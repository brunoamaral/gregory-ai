# Editor tools merged by action

> This is a historical implementation note. For current behaviour, see [Editor access](../07-mcp-server.md#editor-access-mcpeditor).

**Context**: The editor address had 19 tools: ten reads, a history read and eight writes. Several were pairs that undo each other (link and unlink, assign and unassign), and adding patents tool by tool would have taken the list to 26. The tools that do the same thing are now one tool that takes the direction as an argument, so the editor list is 17 with six write tools, and it does not grow when a new kind of record is added.

---

## What changed

The old names are **gone, with no aliases**. A client that still sends one gets the answer for a tool that does not exist. Clients refetch `tools/list` within 5 minutes.

| Old tool | New tool | Arguments |
|:---------|:---------|:----------|
| `get_article_history` | `get_history` | unchanged: `article_id` or `doi`, `limit` |
| `update_article_editorial` | `update_editorial` | unchanged: `article_id` or `doi`, `takeaways`, `summary_plain_english` |
| `link_trial_to_article` | `set_trial_link` | `linked: true`, plus `trial_id` and `article_id` or `doi` |
| `unlink_trial_from_article` | `set_trial_link` | `linked: false`, plus `trial_id` and `article_id` or `doi` |
| `assign_article_category` | `set_category_assignment` | `assigned: true`, plus `category_id` and `article_id` or `doi` |
| `unassign_article_category` | `set_category_assignment` | `assigned: false`, plus `category_id` and `article_id` or `doi` |

`set_article_relevance`, `create_category` and `update_category` are unchanged.

`linked` and `assigned` are required: there is no default direction, so a call can never turn an intended removal into an addition.

## What did not change

- Every call to an old tool has an equivalent call to a new one that makes the same upstream request to Django and returns the same result. Django's routes are untouched, and so are the error messages for each direction.
- Each article tool still takes exactly one of `article_id` and `doi`.
- Write tools still ask the model to show the proposed change and get confirmation first.

## Things that move with the rename

- **Annotations.** The merged write tools can remove as well as add, so every write tool except `create_category` is now `destructive_hint=True`. A client that asks for confirmation on destructive calls will now also ask before an assignment or a link, which were additive tools before.
- **Telemetry.** `tool` in `mcp_request` and `mcp_edit` lines carries the new names from the day this deploys, so counts by tool do not reconcile across that day. `mcp_edit` lines gain `record_type` (`article`), and the two-way tools add `action` (`link`, `unlink`, `assign`, `unassign`), because the tool name alone no longer says which way a call went.
- **Instructions.** The editor instructions name the new tools.
- **Prompts and documents.** A tenant prompt or document (`SiteMcpPrompt`, `SiteMcpDocument`) that names an old tool now points at a tool that does not exist. Check production rows for the six old names before deploying; the research dossier drafts do not name any.
