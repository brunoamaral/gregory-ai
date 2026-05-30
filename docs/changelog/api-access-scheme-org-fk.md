# Runbook — Backfill `APIAccessScheme.organization`

**Context**: PR 2 adds a nullable `organization` FK to `APIAccessScheme`. During the transition window the column is allowed to be `NULL`. PR 7 will reject null-org keys on `POST /articles/post/`, and PR 8 will make the column non-nullable. **All keys must be backfilled before PR 7 ships.**

---

## 1. Audit current keys

Run the following inside the `gregory` container to list every key that still has no organisation assigned:

```bash
docker exec -it gregory python manage.py shell -c "
from api.models import APIAccessScheme
qs = APIAccessScheme.objects.filter(organization__isnull=True)
print(f'{qs.count()} key(s) with no organisation:')
for s in qs:
    print(f'  id={s.pk}  client={s.client_name}  contacts={s.client_contacts}')
"
```

---

## 2. Assign an organisation to a key

**Via Django admin** (recommended):

1. Go to **Admin → API → API access schemes**.
2. Open the key that needs updating.
3. Set the **Organization** dropdown to the correct org.
4. Save.

**Via the Django shell**:

```bash
docker exec -it gregory python manage.py shell -c "
from api.models import APIAccessScheme
from organizations.models import Organization

# Replace with the actual key id and org slug
scheme = APIAccessScheme.objects.get(pk=<KEY_ID>)
org = Organization.objects.get(slug='<ORG_SLUG>')
scheme.organization = org
scheme.save()
print('Done:', scheme)
"
```

---

## 3. Verify backfill is complete

After updating all keys, confirm none are null:

```bash
docker exec -it gregory python manage.py shell -c "
from api.models import APIAccessScheme
count = APIAccessScheme.objects.filter(organization__isnull=True).count()
print('Keys still missing org:', count)
"
```

Expected output: `Keys still missing org: 0`

---

## 4. Notes on null-org keys during the transition window

- **Read endpoints** (articles, trials, authors, etc.): a null-org key is treated as an anonymous caller — it can only see data from organisations where `make_api_public=True`. This matches the behaviour documented in the spec §4.1.
- **`POST /articles/post/`**: null-org keys continue to work until PR 7 ships. After PR 7, they will receive `HTTP 403`.

---

## 5. Timeline

| Event | Action required |
|---|---|
| PR 2 merged | Audit all keys (step 1 above) |
| Before PR 7 ships | All keys must have an `organization` assigned |
| PR 8 ships | Migration enforces `NOT NULL`; any remaining null rows will cause the migration to abort |
