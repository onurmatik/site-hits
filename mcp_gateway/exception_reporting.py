"""Exception-report redaction for OAuth credentials and opaque state."""

from __future__ import annotations

from urllib.parse import urlencode

from django.views.debug import ExceptionReporter, SafeExceptionReporterFilter

_OAUTH_PATH_PREFIX = "/oauth/"
_AUTH_PATH_PREFIX = "/accounts/"
_SENSITIVE_PARAMETERS = frozenset(
    {
        "access_token",
        "client_secret",
        "code",
        "code_verifier",
        "refresh_token",
        "state",
        "token",
    }
)
_QUERY_META_KEYS = frozenset(
    {
        "HTTP_REFERER",
        "QUERY_STRING",
        "RAW_URI",
        "REQUEST_URI",
    }
)


def _privacy_sensitive_path(request) -> bool:
    path = getattr(request, "path", "")
    return path.startswith((_OAUTH_PATH_PREFIX, _AUTH_PATH_PREFIX))


class SiteHitsExceptionReporterFilter(SafeExceptionReporterFilter):
    """Redact OAuth secrets even when an exception precedes view dispatch."""

    def is_active(self, request):
        return _privacy_sensitive_path(request) or super().is_active(request)

    def _cleanse_parameters(self, request, values):
        if not _privacy_sensitive_path(request):
            return super().get_cleansed_multivaluedict(request, values)
        cleansed = values.copy()
        redact_all = getattr(request, "path", "").startswith(_AUTH_PATH_PREFIX)
        for name in cleansed:
            if redact_all or name in _SENSITIVE_PARAMETERS:
                cleansed.setlist(
                    name,
                    [self.cleansed_substitute] * len(cleansed.getlist(name)),
                )
        return cleansed

    def get_cleansed_multivaluedict(self, request, multivaluedict):
        return self._cleanse_parameters(request, multivaluedict)

    def get_post_parameters(self, request):
        if request is None:
            return {}
        return self._cleanse_parameters(request, request.POST)

    def get_query_parameters(self, request):
        if request is None:
            return {}
        return self._cleanse_parameters(request, request.GET)

    def get_safe_request_meta(self, request):
        values = super().get_safe_request_meta(request)
        if _privacy_sensitive_path(request):
            for key in _QUERY_META_KEYS:
                if key in values:
                    values[key] = self.cleansed_substitute
        return values

    def get_traceback_frame_variables(self, request, tb_frame):
        if _privacy_sensitive_path(request):
            return [
                (name, self.cleansed_substitute)
                for name in tb_frame.f_locals
            ]
        return super().get_traceback_frame_variables(request, tb_frame)


class SiteHitsExceptionReporter(ExceptionReporter):
    """Keep raw OAuth query values out of Django traceback renderings."""

    def _get_raw_insecure_uri(self):
        if not _privacy_sensitive_path(self.request):
            return super()._get_raw_insecure_uri()
        query = self.filter.get_query_parameters(self.request)
        encoded = urlencode(list(query.lists()), doseq=True)
        suffix = f"?{encoded}" if encoded else ""
        return (
            f"{self.request.scheme}://{self.request._get_raw_host()}"
            f"{self.request.path}{suffix}"
        )

    def get_traceback_data(self):
        data = super().get_traceback_data()
        if self.request is not None and _privacy_sensitive_path(self.request):
            data["request_GET_items"] = list(
                self.filter.get_query_parameters(self.request).items()
            )
            data["exception_value"] = "OAuth request failure details redacted."
            data["exception_notes"] = None
            data["unicode_hint"] = ""
        return data
