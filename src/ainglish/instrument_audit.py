"""Offline, report-only review of a prospective comprehension instrument.

This is neither qualification nor scientific approval. It does not mint, call readers,
alter assignment, rescore results, or change the server's admission rules.
"""
from collections import Counter, defaultdict
from itertools import product
import re

from ainglish.panel import arm_for
from ainglish.experiment_audit import audit_items


def _normal(text):
    return ' '.join(re.findall(r"\w+", text.casefold()))


def check(manifest, *, axes=('settlement_stratum', 'probe', 'domain')):
    """Inspect a complete, local manifest; never fetch URLs or use reader credentials.

    Axes are explicit item metadata, not inferred scientific classifications. Their
    Cartesian product is reported, including empty intersections. A lexical answer
    overlap is a review flag, never proof of leakage. Repeated semantic_frame values
    are author-declared clustering, not machine detection of semantic equivalence.
    """
    if not isinstance(manifest, dict):
        raise ValueError('manifest must be an object')
    if not isinstance(axes, (tuple, list)) or not all(isinstance(a, str) and a for a in axes) or len(set(axes)) != len(axes):
        raise ValueError('axes must be distinct non-empty field names')
    if len(axes) > 4:
        raise ValueError('at most four explicitly chosen axes may be inspected')
    findings = []

    def flag(code, **details):
        findings.append({'code': code, **details})

    items = manifest.get('items')
    readers = manifest.get('panel')
    if not isinstance(items, list) or not isinstance(readers, list):
        raise ValueError('provide local items and panel arrays; URLs are not fetched')
    if any(not isinstance(i, dict) for i in items + readers):
        raise ValueError('items and readers must be objects')
    real = [i for i in items if not i.get('calibration')]
    if not real or not readers:
        flag('empty_design', targets=len(real), readers=len(readers))
    ids = [i.get('id') for i in items]
    names = [r.get('name') for r in readers]
    for kind, values in [('item', ids), ('reader', names)]:
        valid = [v for v in values if isinstance(v, str) and v.strip()]
        if len(valid) != len(values):
            flag('invalid_identity', identity_kind=kind)
        normalized = [v.strip().casefold() if kind == 'reader' else v.strip() for v in valid]
        duplicates = sorted(k for k, count in Counter(normalized).items() if count > 1)
        if duplicates:
            flag('duplicate_identity', identity_kind=kind, identities=duplicates)

    missing_axes = {axis: [i.get('id') for i in real if not isinstance(i.get(axis), str) or not i[axis].strip()] for axis in axes}
    for axis, missing in missing_axes.items():
        if missing:
            flag('unclassified_axis', axis=axis, item_ids=missing)
    active_axes = [a for a in axes if not missing_axes[a]]
    counts = defaultdict(Counter)
    by_reader = defaultdict(Counter)
    for r in readers:
        if not isinstance(r.get('name'), str) or not r['name'].strip():
            continue
        for item in real:
            if not isinstance(item.get('id'), str) or not item['id'].strip():
                continue
            arm = arm_for(manifest.get('seed', 0), r['name'], item['id'])
            by_reader[r['name']][arm] += 1
            key = (r['name'], *(item[a] for a in active_axes))
            counts[key][arm] += 1
    levels = [sorted({i[a] for i in real}) for a in active_axes]
    size = len(readers)
    for level in levels:
        size *= len(level)
    cells = []
    if size > 10000:
        flag('crossproduct_not_enumerated', planned_cells=size, limit=10000)
    else:
        for name in sorted(by_reader):
            for values in product(*levels):
                c = counts[(name, *values)]
                cell = {'reader': name, 'axes': dict(zip(active_axes, values)),
                        'english': c['english'], 'ainglish': c['ainglish']}
                cells.append(cell)
                if not c:
                    flag('empty_crossed_cell', **cell)
                elif not c['english'] or not c['ainglish']:
                    flag('single_arm_cell', **cell)
    for name, c in sorted(by_reader.items()):
        if not c['english'] or not c['ainglish']:
            flag('single_arm_reader', reader=name, english=c['english'], ainglish=c['ainglish'])

    frames = defaultdict(list)
    missing_frames = []
    for item in real:
        iid = item.get('id')
        frame = item.get('semantic_frame')
        if isinstance(frame, str) and frame.strip():
            frames[frame].append(iid)
        else:
            missing_frames.append(iid)
        answer = item.get('answer')
        options = item.get('options')
        if not isinstance(answer, str) or not isinstance(options, list) or options.count(answer) != 1:
            flag('invalid_answer_key', item_id=iid)
            continue
        gold = _normal(answer)
        # Short choices such as "yes" or an opaque code are not a meaningful lexical
        # leakage screen. The declared limits must stay visible in the report.
        if len(gold.split()) < 3:
            continue
        overlap = [arm for arm in ('english', 'ainglish') if isinstance(item.get(arm), str) and
                   (' ' + gold + ' ') in (' ' + _normal(item[arm]) + ' ')]
        if overlap:
            flag('gold_phrase_in_arm_review', item_id=iid, arms=overlap)
    for frame, group in sorted(frames.items()):
        if len(group) > 1:
            flag('shared_declared_frame', semantic_frame=frame, item_ids=group)
    if missing_frames:
        flag('semantic_independence_unassessed', item_ids=missing_frames)
    return {
        'kind': 'ainglish.instrument-audit.v1', 'report_only': True,
        'target_items': len(real), 'reader_count': len(readers),
        'requested_axes': list(axes), 'assessed_axes': active_axes,
        'item_audit': audit_items(items),
        'cells': cells, 'findings': findings,
        'limits': [
            'No finding is an automatic invalidation, qualification, admission decision, or evidence result.',
            'Empty intersections may be intentional; tiny cells may lack both arms by chance. Review against the declared design.',
            'Missing axis or frame metadata is unassessed, not evidence of balance or independence.',
            'Frame identity is supplied by the author. Different frame labels do not prove semantic independence.',
            'Lexical overlap checks only complete normalized gold phrases of at least three words; it can miss shortcuts and can flag legitimate text.',
            'No outputs were inspected; balance cannot establish a sound comparator, key, estimand, power, or model independence.',
        ],
    }
