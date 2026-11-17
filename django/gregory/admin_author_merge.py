"""The "Author merge candidates" admin screen. Superusers only: every view here refuses anyone
else with a 403, and the entry is absent from other users' admin index. Nothing here is
exposed outside the admin (no API, MCP, RSS or serializer).

Candidates are computed live from ``Authors.openalex_id`` (see
``gregory.services.author_merge_candidates``); the only stored thing is the dismissal.
Merging a group reloads and re-classifies it first, so a stale page can't merge a group
that has since become blocked.
"""

from urllib.parse import urlencode

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from .admin_labs import SuperuserOnlyAdminMixin
from .models import AuthorMergeCandidate, AuthorMergeDismissal
from .services import author_merge_candidates as svc

CLASS_LABELS = [
	(svc.CLEAR_CUT, "Clear-cut"),
	(svc.NAMES_DIFFER, "Names differ"),
	(svc.BLOCKED, "Blocked"),
	(svc.DISMISSED, "Dismissed"),
]
VALID_CLASSES = {key for key, _ in CLASS_LABELS}


def _screen_params(source):
	"""The class / search / page of the screen, from a GET or POST dict, whitelisted."""
	params = {}
	status = source.get("class", "")
	if status in VALID_CLASSES:
		params["class"] = status
	if source.get("q", "").strip():
		params["q"] = source["q"].strip()
	if source.get("page", "").isdigit():
		params["page"] = source["page"]
	return params


def _screen_url(params):
	base = reverse("admin:gregory_authormergecandidate_changelist")
	return f"{base}?{urlencode(params)}" if params else base


def _int_list(values):
	return [int(v) for v in values if str(v).isdigit()]


class AuthorMergeCandidateAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
	def get_urls(self):
		wrap = self.admin_site.admin_view
		return [
			path("merge/", wrap(self.merge_view), name="gregory_authormergecandidate_merge"),
			path("dismiss/", wrap(self.dismiss_view), name="gregory_authormergecandidate_dismiss"),
			path("restore/", wrap(self.restore_view), name="gregory_authormergecandidate_restore"),
			path("merge-all/", wrap(self.merge_all_view), name="gregory_authormergecandidate_merge_all"),
		] + super().get_urls()

	def _require_superuser(self, request):
		if not request.user.is_superuser:
			raise PermissionDenied

	def changelist_view(self, request, extra_context=None):
		self._require_superuser(request)
		params = _screen_params(request.GET)
		status = params.get("class", svc.CLEAR_CUT)
		search = params.get("q", "")
		groups = svc.load_groups()
		counts = svc.count_by_class(groups)
		shown = svc.filter_groups(groups, status, search)
		page = Paginator(shown, svc.PAGE_SIZE).get_page(params.get("page", 1))
		svc.attach_labs(page.object_list)
		base = {k: v for k, v in params.items() if k != "page"}
		context = {
			**self.admin_site.each_context(request),
			"title": "Author merge candidates",
			"opts": self.model._meta,
			"page": page,
			"status": status,
			"search": search,
			"counts": counts,
			"tabs": [
				{
					"key": key,
					"label": label,
					"count": counts[key],
					"url": _screen_url({**({"q": search} if search else {}), "class": key}),
					"active": key == status,
				}
				for key, label in CLASS_LABELS
			],
			"return_params": params,
			"pagination_base": urlencode(base),
			"merge_url": reverse("admin:gregory_authormergecandidate_merge"),
			"dismiss_url": reverse("admin:gregory_authormergecandidate_dismiss"),
			"restore_url": reverse("admin:gregory_authormergecandidate_restore"),
			"merge_all_url": reverse("admin:gregory_authormergecandidate_merge_all"),
			"bulk_batch_size": svc.BULK_BATCH_SIZE,
		}
		return TemplateResponse(
			request, "admin/gregory/authormergecandidate/change_list.html", context
		)

	def _redirect(self, request):
		return HttpResponseRedirect(_screen_url(_screen_params(request.POST)))

	def merge_view(self, request):
		self._require_superuser(request)
		if request.method != "POST":
			return HttpResponseNotAllowed(["POST"])
		openalex_id = request.POST.get("openalex_id", "")
		keep = _int_list([request.POST.get("keep", "")])
		merging = _int_list(request.POST.getlist("merge"))
		if not keep or keep[0] not in merging or len(merging) < 2:
			self.message_user(
				request,
				"Tick the rows to merge (at least two) and choose the survivor among them.",
				level=messages.ERROR,
			)
			return self._redirect(request)
		try:
			outcome = svc.merge_group(openalex_id, keep[0], [i for i in merging if i != keep[0]])
		except svc.MergeRefused as exc:
			self.message_user(request, str(exc), level=messages.ERROR)
		except Exception:
			svc.logger.exception("Author merge failed for OpenAlex ID %s", openalex_id)
			self.message_user(
				request,
				"Merge failed due to an internal error. Check the server logs for details.",
				level=messages.ERROR,
			)
		else:
			self.message_user(
				request,
				f"Merged {outcome.removed} author(s) into ID {outcome.kept}: {outcome.articles} article "
				f"link(s), {outcome.authorships} authorship(s), {outcome.outreach_rows} outreach row(s) "
				f"and {outcome.opt_outs} opt-out(s) moved.",
			)
		return self._redirect(request)

	def dismiss_view(self, request):
		self._require_superuser(request)
		if request.method != "POST":
			return HttpResponseNotAllowed(["POST"])
		openalex_id = request.POST.get("openalex_id", "")
		shown_ids = sorted(_int_list(request.POST.getlist("ids")))
		groups = svc.load_groups([openalex_id])
		if not groups or list(groups[0].ids) != shown_ids:
			self.message_user(
				request, "The group changed since it was shown; reload and try again.", level=messages.ERROR
			)
			return self._redirect(request)
		AuthorMergeDismissal.objects.create(
			openalex_id=openalex_id,
			author_ids=shown_ids,
			dismissed_by=request.user,
			note=request.POST.get("note", "").strip(),
		)
		self.message_user(request, f"Marked {openalex_id} as not the same person.")
		return self._redirect(request)

	def restore_view(self, request):
		self._require_superuser(request)
		if request.method != "POST":
			return HttpResponseNotAllowed(["POST"])
		openalex_id = request.POST.get("openalex_id", "")
		deleted, _ = AuthorMergeDismissal.objects.filter(openalex_id=openalex_id).delete()
		self.message_user(request, f"Restored {openalex_id} to the candidates ({deleted} dismissal(s) removed).")
		return self._redirect(request)

	def merge_all_view(self, request):
		self._require_superuser(request)
		groups = svc.load_groups()
		todo = [g for g in groups if g.status == svc.CLEAR_CUT and not g.dismissed]
		if request.method == "POST":
			try:
				result = svc.merge_clear_cut_groups(
					limit=svc.BULK_BATCH_SIZE,
					time_budget=svc.BULK_TIME_BUDGET_SECONDS,
					groups=groups,
				)
			except Exception:
				svc.logger.exception("Bulk author merge failed")
				self.message_user(request, "Bulk merge failed; see the server logs.", level=messages.ERROR)
				return HttpResponseRedirect(_screen_url({}))
			text = (
				f"Merged {result.groups_merged} clear-cut group(s), removing {result.rows_removed} "
				f"author row(s) and moving {result.articles} article link(s)."
			)
			if result.failed:
				text += f" {result.failed} group(s) could not be merged (changed or errored)."
			if result.remaining:
				text += f" {result.remaining} clear-cut group(s) left: run it again."
			self.message_user(request, text)
			return HttpResponseRedirect(_screen_url({}))
		context = {
			**self.admin_site.each_context(request),
			"title": "Merge all clear-cut groups",
			"opts": self.model._meta,
			"total": len(todo),
			"sample": todo[:20],
			"batch_size": svc.BULK_BATCH_SIZE,
			"batches": -(-len(todo) // svc.BULK_BATCH_SIZE),
			"cancel_url": _screen_url({}),
		}
		return TemplateResponse(
			request, "admin/gregory/authormergecandidate/merge_all_confirmation.html", context
		)


def register(site):
	site.register(AuthorMergeCandidate, AuthorMergeCandidateAdmin)
