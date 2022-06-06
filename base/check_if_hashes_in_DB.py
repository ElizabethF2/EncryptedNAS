import EncryptedNAS, os, sqlite3

print('Input Path:')
path = input('>')
if path.startswith('"') and path.endswith('"'):
  path = path[1:-1]

hashes = []
for root, dirs, files in os.walk(path):
  for file in files:
    fp = os.path.join(root, file)
    print('Hashing ' + fp + '...')
    hash = EncryptedNAS.hash_file(fp)
    hashes.append((fp, hash))

print('Got ' + str(len(hashes)) + ' hash(es)')

print('Checking against database...')
missing = []
with sqlite3.connect(EncryptedNAS.config['database']) as conn:
  c = conn.cursor()
  for fp, hash in hashes:
    c.execute('SELECT * FROM Files WHERE Hash = (?)', (hash,))
    r = c.fetchone()
    if r is None:
      missing.append(fp)

print('Done!')

print('')
print('Missing Files: ' + str(len(missing)))
if len(missing) > 0:
  print('These files are missing from the database:')
  for file in missing:
    print('  ' + file)
