"""Which reported helpers fill which position of a duty.

A helper is reported with their qualification (``Report.helper_qualification``). They
can fill the position for it or one whose qualification theirs includes (e.g. a paramedic
a first aider's place, see ``Qualification.includes``). They are reported for the closest
of these positions, their own one (``Report.position``).

Confirmed helpers fill a fixed position (``Report.assigned_position``), which also sets
their pay: when they are confirmed, the free position closest to their qualification; the
dispatchers can change it. Pending helpers are only counted, in the places left. Helpers
who fit nowhere count for their own position, which is then overstaffed.

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


def fitting(qualification_id, positions, includes):
    """The positions a helper with the qualification can fill, closest first."""
    qualifications = chain(qualification_id, includes)
    found = [p for p in positions if p.qualification_id in qualifications]
    # Down the chain.
    return sorted(found, key=lambda p: qualifications.index(p.qualification_id))


def assign(qualification_id, own, positions, filled, includes):
    """The position a newly confirmed helper with the qualification fills.

    ``filled`` counts the confirmed helpers per position ID. The free position closest to
    their qualification, otherwise their own position ``own`` anyway.
    """
    for position in fitting(qualification_id, positions, includes):
        if filled[position.pk] < position.count:
            return position
    return own


def count(positions, reports, includes):
    """Sets ``confirmed_count`` and ``pending_count`` on the positions of a duty.

    ``reports`` are (qualification ID, reported position ID, assigned position ID, status)
    of the active reports.
    """
    from .models import Report

    by_id = {p.pk: p for p in positions}
    confirmed = Counter(
        assigned_id or position_id
        for _qualification_id, position_id, assigned_id, status in reports
        if status == Report.Status.CONFIRMED
    )
    for position in positions:
        position.confirmed_count = confirmed[position.pk]
        position.pending_count = 0
    free = {p.pk: max(p.count - confirmed[p.pk], 0) for p in positions}
    # Pending helpers by the position they were reported for and their qualification.
    pending = Counter(
        (position_id, qualification_id)
        for qualification_id, position_id, _assigned_id, status in reports
        if status == Report.Status.REPORTED and position_id in by_id
    )
    chains = {p.pk: chain(p.qualification_id, includes) for p in positions}
    helper_chains = {key: chain(key[1], includes) for key in pending}
    for position in sorted(positions, key=lambda p: -len(chains[p.pk])):
        # Helpers reported for this very position first.
        for key in sorted(pending, key=lambda key: key[0] != position.pk):
            if not free[position.pk]:
                break
            if pending[key] and position.qualification_id in helper_chains[key]:
                taken = min(pending[key], free[position.pk])
                pending[key] -= taken
                free[position.pk] -= taken
                position.pending_count += taken
    # The rest overstaff their own position.
    for (own_id, _qualification_id), number in pending.items():
        by_id[own_id].pending_count += number
