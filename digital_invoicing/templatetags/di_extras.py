from django import template

register = template.Library()


@register.filter
def get_item(d, key):
    """Dict lookup by variable key (eligible_map|get_item:b.pk)."""
    try:
        return d.get(key, [])
    except AttributeError:
        return []
