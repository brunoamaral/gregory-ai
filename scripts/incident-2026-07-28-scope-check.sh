#!/bin/bash
#
# Scope check for the 2026-07-28 site-scope unsubscribe incident
#
# Closes two of the three open items in
# docs/incidents/2026-07-28-site-scope-unsubscribe-not-honoured.md:
#
#   1. Replace the indicative development figures in that record with real
#      production numbers.
#   2. Establish whether web server access logs cover the exposure window
#      (2026-04-16 onwards). The unsubscribe token is in the URL path, so a
#      POST to /subscriptions/unsubscribe/<token>/site/<id>/ identifies the
#      subscriber who asked to be unsubscribed and was not. If those log
#      lines exist, those requests can still be honoured retroactively.
#
# READ-ONLY. This script runs SELECT queries and greps log files. It sends no
# email, writes nothing to the database, and changes no subscription state.
# Nothing here acts on what it finds — honouring any requests it turns up is a
# separate, deliberate step.
#
# Usage, on the production host:
#   ./scripts/incident-2026-07-28-scope-check.sh
#
# Configuration:
#   CONTAINER   Django container name (default: gregory)
#   LOG_GLOB    Access log glob (default: /var/log/nginx/access.log*)
#   START_DATE  Exposure window start (default: 2026-04-16)
#

set -euo pipefail

CONTAINER="${CONTAINER:-gregory}"
LOG_GLOB="${LOG_GLOB:-/var/log/nginx/access.log*}"
START_DATE="${START_DATE:-2026-04-16}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=== Incident scope check — site-scope unsubscribe (2026-07-28) ===${NC}"
echo "Container: $CONTAINER"
echo "Log glob:  $LOG_GLOB"
echo "Window:    $START_DATE onwards"
echo

# ---------------------------------------------------------------------------
# Part 1 — production figures for the incident record
# ---------------------------------------------------------------------------
echo -e "${BLUE}--- Part 1: production scope figures (read-only) ---${NC}"

docker exec "$CONTAINER" python manage.py shell -c "
from datetime import datetime, timezone
from subscriptions.models import (
	Subscribers, ListSubscription, SentArticleNotification, SentTrialNotification, Lists
)

start = datetime.fromisoformat('${START_DATE}').replace(tzinfo=timezone.utc)

print('Subscriber totals')
print('  total subscriber records:            ', Subscribers.objects.count())
print('  with >=1 active subscription:        ',
      ListSubscription.objects.filter(is_active=True).values('subscriber').distinct().count())

print()
print('Recipients exposed to the broken link (lower bound)')
subs = set(
	SentArticleNotification.objects.filter(sent_at__gte=start).values_list('subscriber_id', flat=True)
) | set(
	SentTrialNotification.objects.filter(sent_at__gte=start).values_list('subscriber_id', flat=True)
)
print('  distinct subscribers with a retained send record since ${START_DATE}:', len(subs))
oldest_a = SentArticleNotification.objects.order_by('sent_at').values_list('sent_at', flat=True).first()
oldest_t = SentTrialNotification.objects.order_by('sent_at').values_list('sent_at', flat=True).first()
print('  oldest retained article send record: ', oldest_a)
print('  oldest retained trial send record:   ', oldest_t)
print('  NOTE: send records are pruned after 30 days, so the number above is a')
print('        lower bound — it cannot reach back to ${START_DATE}.')

print()
print('Unsubscribe requests that DID work in the window (the two other links)')
print('  ListSubscription rows deactivated since ${START_DATE}:',
      ListSubscription.objects.filter(is_active=False, unsubscribed_at__gte=start).count())

print()
print('Lists.site vs Team.site — the mismatch that caused the bug')
for l in Lists.objects.select_related('team', 'site').all().order_by('list_id'):
	print(f'  list {l.list_id:>3} {l.list_name[:30]:<30} list.site={l.site_id} team.site={l.team.site_id}')
"

echo
# ---------------------------------------------------------------------------
# Part 2 — do the access logs reach the exposure window?
# ---------------------------------------------------------------------------
echo -e "${BLUE}--- Part 2: access log coverage and site-scope unsubscribe hits ---${NC}"

shopt -s nullglob
LOGS=( $LOG_GLOB )
shopt -u nullglob

if [ ${#LOGS[@]} -eq 0 ]; then
	echo -e "${YELLOW}No log files matched $LOG_GLOB.${NC}"
	echo "Set LOG_GLOB to the right path and re-run, e.g.:"
	echo "  LOG_GLOB='/var/log/nginx/*access*' $0"
	exit 0
fi

# From here on, a single unreadable or malformed log file must not abort the
# run — nginx logs are commonly root:adm 640, and losing the whole report to
# one permission error is exactly the failure this section is diagnosing.
set +e

# read_log <file> — emit the file's contents, or nothing if it can't be read.
# Uses `gzip -cd` rather than `zcat`: macOS zcat appends .Z and silently emits
# nothing for a .gz file, which would have made rotated logs look empty.
read_log() {
	case "$1" in
		*.gz) gzip -cd "$1" 2>/dev/null ;;
		*)    cat "$1" 2>/dev/null ;;
	esac
}

to_iso() {
	# "[15/Jul/2026" -> "2026-07-15"
	local s="${1#[}" d m y
	d="${s%%/*}"; s="${s#*/}"; m="${s%%/*}"; y="${s#*/}"
	case "$m" in
		Jan) m=01;; Feb) m=02;; Mar) m=03;; Apr) m=04;; May) m=05;; Jun) m=06;;
		Jul) m=07;; Aug) m=08;; Sep) m=09;; Oct) m=10;; Nov) m=11;; Dec) m=12;;
		*) return 1;;
	esac
	printf '%s-%s-%s' "$y" "$m" "$d"
}

echo "Matched ${#LOGS[@]} log file(s). Oldest entry per file:"
UNREADABLE=0
READABLE=0
OLDEST_ISO=""
for f in "${LOGS[@]}"; do
	if [ ! -r "$f" ]; then
		printf '  %-45s %s\n' "$(basename -- "$f")" "<not readable by $(id -un)>"
		UNREADABLE=$((UNREADABLE + 1))
		continue
	fi
	READABLE=$((READABLE + 1))
	first=$(read_log "$f" | head -1)
	# Nginx combined format puts the timestamp in [dd/Mon/yyyy:hh:mm:ss +zzzz]
	stamp=$(printf '%s' "$first" | grep -oE '\[[0-9]{2}/[A-Za-z]{3}/[0-9]{4}' | head -1)
	printf '  %-45s %s\n' "$(basename -- "$f")" "${stamp:-<no timestamp found>}"
	if [ -n "$stamp" ]; then
		iso=$(to_iso "$stamp")
		if [ -n "$iso" ] && { [ -z "$OLDEST_ISO" ] || [[ "$iso" < "$OLDEST_ISO" ]]; }; then
			OLDEST_ISO="$iso"
		fi
	fi
done

if [ "$UNREADABLE" -gt 0 ]; then
	echo
	echo -e "${YELLOW}$UNREADABLE of ${#LOGS[@]} log file(s) are not readable by $(id -un).${NC}"
	echo "Nginx logs are usually root:adm 640. Re-run with sudo to see all of them:"
	echo "  sudo -E $0"
	if [ "$READABLE" -eq 0 ]; then
		echo
		echo -e "${YELLOW}No readable logs at all — the scan below would be meaningless.${NC}"
		echo -e "${BLUE}=== Stopped. Nothing was modified. ===${NC}"
		exit 0
	fi
	echo "Continuing with the $READABLE readable file(s) — treat the result as partial."
fi

echo
echo "Site-scope unsubscribe requests found (token redacted):"
FOUND=0
for f in "${LOGS[@]}"; do
	[ -r "$f" ] || continue
	# Match the site-scope path only. The list and all scopes worked correctly
	# and are deliberately excluded.
	while IFS= read -r line; do
		FOUND=$((FOUND + 1))
		printf '%s\n' "$line" \
			| sed -E 's#(/subscriptions/unsubscribe/)[0-9a-fA-F-]{36}(/site/)#\1<TOKEN>\2#'
	done < <(read_log "$f" \
		| grep -E 'POST /subscriptions/unsubscribe/[0-9a-fA-F-]{36}/site/[0-9]+/')
done

echo
# Base rate for comparison. "Zero site-scope requests" is only meaningful if
# the same logs show people using the OTHER unsubscribe links over the same
# period — otherwise it just means nobody unsubscribed at all, or the logs
# don't cover a period when anyone was mailed.
LIST_HITS=0
ALL_HITS=0
for f in "${LOGS[@]}"; do
	[ -r "$f" ] || continue
	n=$(read_log "$f" | grep -cE 'POST /subscriptions/unsubscribe/[0-9a-fA-F-]{36}/list/[0-9]+/')
	LIST_HITS=$((LIST_HITS + n))
	n=$(read_log "$f" | grep -cE 'POST /subscriptions/unsubscribe/[0-9a-fA-F-]{36}/all/')
	ALL_HITS=$((ALL_HITS + n))
done

echo "Base rate over the same logs, for comparison:"
echo "  list-scope unsubscribe POSTs (worked correctly): $LIST_HITS"
echo "  all-scope  unsubscribe POSTs (worked correctly): $ALL_HITS"
echo "  site-scope unsubscribe POSTs (the broken one):   $FOUND"
echo
echo "If the two working scopes show real traffic and site-scope shows none,"
echo "that is evidence the broken link was rarely used. If all three are zero,"
echo "the logs simply do not cover a period when anyone unsubscribed, and"
echo "nothing can be concluded from them either way."

# Disambiguate an all-zero base rate. If the database shows unsubscribes inside
# the log-covered window but the logs show no unsubscribe POSTs at all, then
# these logs are not capturing the endpoint (a different vhost, a different log
# file, or a proxy in front) — and the whole log-based approach is invalid
# rather than merely inconclusive.
if [ $((LIST_HITS + ALL_HITS + FOUND)) -eq 0 ] && [ -n "$OLDEST_ISO" ]; then
	echo
	echo -e "${BLUE}All scopes zero — checking whether anyone unsubscribed in that period at all${NC}"
	echo "Log-covered window starts: $OLDEST_ISO"
	docker exec "$CONTAINER" python manage.py shell -c "
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from subscriptions.models import FailedNotification, ListSubscription

start = datetime.fromisoformat('${OLDEST_ISO}').replace(tzinfo=timezone.utc)
rows = list(
	ListSubscription.objects.filter(is_active=False, unsubscribed_at__gte=start)
	.select_related('subscriber', 'list')
	.order_by('unsubscribed_at')
)
print('  ListSubscription rows deactivated since ${OLDEST_ISO}:', len(rows))

if not rows:
	print()
	print('  => Nobody unsubscribed during the logged period. The all-zero result')
	print('     above is consistent and simply uninformative: these logs cover a')
	print('     quiet stretch. No conclusion can be drawn about the broken link.')
else:
	# A ListSubscription can be deactivated with no HTTP request at all:
	# the admin 'Disable all emails' action and the Postmark-406 suppression
	# handler both call deactivate_subscribers(), which deactivates every one
	# of a subscriber's subscriptions at the same instant. A person clicking
	# a link produces one row (list scope) or a set the view wrote directly.
	# Only rows that cannot be explained that way imply a missing log entry.
	buckets = defaultdict(list)
	for r in rows:
		buckets[(r.subscriber_id, r.unsubscribed_at.replace(microsecond=0))].append(r)

	suppressed = bulk = singles = 0
	for (sub_id, ts), group in sorted(buckets.items(), key=lambda kv: kv[0][1]):
		near_406 = FailedNotification.objects.filter(
			subscriber_id=sub_id,
			reason__contains='406',
			created_at__gte=ts - timedelta(hours=1),
			created_at__lte=ts + timedelta(hours=1),
		).exists()
		email = group[0].subscriber.email
		masked = email[:2] + '***@' + email.split('@')[-1]
		if near_406:
			label = 'Postmark 406 suppression'
			suppressed += 1
		elif len(group) > 1:
			label = 'bulk (all subscriptions at once — admin action or suppression)'
			bulk += 1
		else:
			label = 'single row — consistent with someone clicking a link'
			singles += 1
		print(f'    {ts:%Y-%m-%d %H:%M} {masked:<28} {len(group)} row(s)  {label}')

	print()
	print(f'  suppression-linked: {suppressed}   bulk: {bulk}   single: {singles}')
	print()
	if singles == 0:
		print('  => Every deactivation in the logged period is explained by an admin')
		print('     action or Postmark suppression, neither of which makes an HTTP')
		print('     request. The absence of unsubscribe POSTs in the logs is therefore')
		print('     expected, and the logs are NOT shown to be the wrong ones. The')
		print('     all-zero base rate stays uninformative rather than invalid.')
	else:
		print(f'  => WARNING: {singles} deactivation(s) look like link clicks, yet the logs')
		print('     contain no unsubscribe POSTs of any scope. That suggests these logs')
		print('     are not capturing the unsubscribe endpoint — check whether it is')
		print('     served by a different nginx vhost or log file (the links are built')
		print('     from CustomSetting.api_domain, which may point at a host logging')
		print('     elsewhere). Until resolved, treat the log evidence as invalid.')
"
fi

echo
if [ "$FOUND" -eq 0 ]; then
	echo -e "${YELLOW}No site-scope unsubscribe POSTs found in the available logs.${NC}"
	echo "Either nobody used that link, or the logs no longer reach back far enough."
	echo "Compare the oldest-entry dates above against $START_DATE before concluding."
else
	echo -e "${GREEN}Found $FOUND site-scope unsubscribe POST(s).${NC}"
	echo
	echo "Each corresponds to someone who asked to be unsubscribed and was not."
	echo "To identify and honour them, extract the unredacted tokens and match on"
	echo "Subscribers.unsubscribe_token. Tokens are credentials — treat that output"
	echo "as sensitive, and do it as a deliberate follow-up rather than from here."
fi

echo
echo -e "${BLUE}=== Done. Nothing was modified. ===${NC}"
echo "Record the results in docs/incidents/2026-07-28-site-scope-unsubscribe-not-honoured.md"
