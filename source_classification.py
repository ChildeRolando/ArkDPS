"""Classify statistic keys that have no reliable owner mapping.

Whole-team shares need every team source attributed. A source that cannot be
mapped is normally unknown and must keep the team denominator unknown. The one
exception is a source that is *proven* not to be a squad member: nothing it
outputs can belong in the team denominator, so it is reported as an excluded
non-team source instead of blocking the whole panel.

Only verified client prefab namespaces are recognised. Squad operators use
``char_``, summons/deployables use ``token_`` and map devices/traps use
``trap_`` (the same split the deployment reader relies on). Everything else --
including any ``char_``/``token_`` key without a unique host -- stays unknown,
because misclassifying it would silently drop real team output.
"""

NON_TEAM_CLASSES = (
    ('trap_', 'map_trap', '非队伍地图装置/陷阱'),
)


def classify_unmapped_source(cid, roster_cids=()):
    """Return ``(kind, label)`` for a proven non-team source, else ``None``.

    ``roster_cids`` are the statistic keys the current roster already accounts
    for (squad operators, mapped summons or independently attributed map
    units). A key present there is never classified: its ownership belongs to
    the roster evidence, not to a naming convention.
    """
    if not isinstance(cid, str) or not cid or cid in roster_cids:
        return None
    for prefix, kind, label in NON_TEAM_CLASSES:
        if cid.startswith(prefix):
            return kind, label
    return None
