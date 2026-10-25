"""Which reported helpers fill which position of a duty.

A helper is reported for a position, i.e. with its qualification. They can fill that
position or one whose qualification theirs includes (e.g. a paramedic a first aider's
place, see ``Qualification.includes``).

Confirmed helpers fill a fixed position (``Report.assigned_position``), which also sets
their pay: when they are confirmed, their own position if a place is free, otherwise the
free position closest to their qualification; the dispatchers can change it. Pending
helpers are only counted, in the places left. Helpers who fit nowhere count for their own
position, which is then overstaffed.

The most specific positions are filled first, i.e. those whose qualification includes the
most others: whoever can fill one of them can also fill every position that is filled
later, so no helper is wasted on a position others could have filled instead.
"""

from collections import Counter


def chain(qualification_id, includes):
    """The qualification and those it includes, directly or indirectly.

    ``includes`` maps the ID of each qualification to the ID of the one it includes.
    """
    found = []
    while qualification_id is not None and qualification_id not in found:
        found.append(qualification_id)
        qualification_id = includes.get(qualification_id)
    return found


def fitting(own, positions, includes):
    """The positions a helper reported for the position ``own`` can fill, closest first."""
    if own is None:
        return []
    qualifications = chain(own.qualification_id, includes)
    found = [p for p in positions if p.qualification_id in qualifications]
    # Their own position first, then down the chain.
    return sorted(found, key=lambda p: qualifications.index(p.qualification_id))


def assign(own, positions, filled, includes):
    """The position a newly confirmed helper fills.

    ``filled`` counts the confirmed helpers per position ID. Their own position if a place
    is free there, otherwise the free position closest to their qualification, otherwise
    their own one anyway.
    """
    for position in fitting(own, positions, includes):
        if filled[position.pk] < position.count:
            return position
    return own


def count(positions, reports, includes):
    """Sets ``confirmed_count`` and ``pending_count`` on the positions of a duty.

    ``reports`` are (reported position ID, assigned position ID, status) of the active
    reports.
    """
    from .models import Report

    by_id = {p.pk: p for p in positions}
    confirmed = Counter(
        assigned_id or position_id
        for position_id, assigned_id, status in reports
        if status == Report.Status.CONFIRMED
    )
    for position in positions:
        position.confirmed_count = confirmed[position.pk]
        position.pending_count = 0
    free = {p.pk: max(p.count - confirmed[p.pk], 0) for p in positions}
    # Pending helpers by the position they were reported for.
    pending = Counter(
        position_id
        for position_id, _assigned_id, status in reports
        if status == Report.Status.REPORTED and position_id in by_id
    )
    chains = {p.pk: chain(p.qualification_id, includes) for p in positions}
    for position in sorted(positions, key=lambda p: -len(chains[p.pk])):
        # Helpers reported for this very position first.
        for own_id in sorted(pending, key=lambda own_id: own_id != position.pk):
            if not free[position.pk]:
                break
            if pending[own_id] and position.qualification_id in chains[own_id]:
                taken = min(pending[own_id], free[position.pk])
                pending[own_id] -= taken
                free[position.pk] -= taken
                position.pending_count += taken
    # The rest overstaff their own position.
    for own_id, number in pending.items():
        by_id[own_id].pending_count += number
