import json
from collections import defaultdict
from datetime import datetime

with open('/home/baw/.baw/knowledge_graph.json', 'r') as f:
    kg = json.load(f)

triples = kg.get('triples', [])
entities = kg.get('entities', {})
entity_names = set(entities.keys())
noise_rels = {'is_a', 'type_of', 'related_to', 'has', 'part_of', 'mentioned_in', 'instance_of'}

before_triples = len(triples)
before_entities = len(entities)
before_relations = len(set(t.get('r') for t in triples))

to_remove_indices = set()
noise_triples = []
orphaned_triples = []
duplicate_triples = []

seen = set()
for i, t in enumerate(triples):
    key = (t.get('s'), t.get('r'), t.get('o'))
    if key in seen:
        duplicate_triples.append(t)
        to_remove_indices.add(i)
    seen.add(key)
    if t.get('r') in noise_rels:
        noise_triples.append(t)
        to_remove_indices.add(i)
    if t.get('s') not in entity_names or t.get('o') not in entity_names:
        orphaned_triples.append(t)
        to_remove_indices.add(i)

curated_triples = [t for i, t in enumerate(triples) if i not in to_remove_indices]

conn = defaultdict(int)
for t in curated_triples:
    conn[t.get('s')] += 1
    conn[t.get('o')] += 1

isolated_entities = [e for e, c in conn.items() if c <= 1]
empty_relation_entities = [k for k, v in entities.items() if not v.get('relations')]
entities_to_remove = set(isolated_entities) | set(empty_relation_entities)
curated_entities = {k: v for k, v in entities.items() if k not in entities_to_remove}

after_triples = len(curated_triples)
after_entities = len(curated_entities)
after_relations = len(set(t.get('r') for t in curated_triples))

kg['triples'] = curated_triples
kg['entities'] = curated_entities
kg['_curated_at'] = datetime.utcnow().isoformat() + 'Z'
kg['_curation_summary'] = {
    'removed_triples': len(to_remove_indices),
    'removed_entities': len(entities_to_remove),
    'noise_triples': len(noise_triples),
    'orphaned_triples': len(orphaned_triples),
    'duplicate_triples': len(duplicate_triples),
    'isolated_entities_removed': len(isolated_entities),
    'empty_relation_entities_removed': len(empty_relation_entities)
}

with open('/home/baw/.baw/knowledge_graph.json', 'w') as f:
    json.dump(kg, f, indent=2)

print(f"Triples: {before_triples} -> {after_triples} (removed {len(to_remove_indices)})")
print(f"Entities: {before_entities} -> {after_entities} (removed {len(entities_to_remove)})")
print(f"Relations: {before_relations} -> {after_relations}")
print(f"Noise: {len(noise_triples)}, Orphaned: {len(orphaned_triples)}, Dupes: {len(duplicate_triples)}")
