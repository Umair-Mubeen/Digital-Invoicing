"""Template context — har page ko FBR mode ka pata (topbar chip)."""
from django.conf import settings


def fbr_mode(request):
    return {"fbr_mock": getattr(settings, "FBR_USE_MOCK", True)}
