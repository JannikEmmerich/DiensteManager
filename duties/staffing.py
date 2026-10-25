"""Which reported helpers fill which position of a duty.

A helper counts for the position they were reported for, for a position whose
qualification theirs includes (e.g. a paramedic for a first aider, see
``Qualification.includes``), or for a position with any qualification. Confirmed helpers
are placed first, then the pending ones in the places left. Helpers who fit nowhere count
for their own position, which is then overstaffed.

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


def count(positions, reports, includes):
    """Sets ``confirmed_count`` and ``pending_count`` on the positions of a duty.

    ``reports`` are the (position ID, status) pairs of the active reports.
    """
    from .models import Report

    chains = {p.pk: chain(p.qualification_id, includes) for p in positions}

    def fits(own_id, position):
        if position.qualification_id is None:
            return True  # any qualification
        return position.qualification_id in chains.get(own_id, ())

    # Most specific first; positions for any qualification last.
    order = sorted(positions, key=lambda p: -len(chains[p.pk]) if p.qualification_id else 1)
    free = {p.pk: p.count for p in positions}
    for attribute, status in [
        ("confirmed_count", Report.Status.CONFIRMED),
        ("pending_count", Report.Status.REPORTED),
    ]:
        # Helpers by the position they were reported for (None: none any more).
        helpers = Counter(position_id for position_id, s in reports if s == status)
        placed = Counter()
        for position in order:
            # Helpers reported for this very position first.
            for own_id in sorted(helpers, key=lambda own_id: own_id != position.pk):
                if not free[position.pk]:
                    break
                if helpers[own_id] and fits(own_id, position):
                    taken = min(helpers[own_id], free[position.pk])
                    helpers[own_id] -= taken
                    free[position.pk] -= taken
                    placed[position.pk] += taken
        # The rest overstaff their own position.
        placed.update({own_id: number for own_id, number in helpers.items() if own_id})
        for position in positions:
            setattr(position, attribute, placed[position.pk])
