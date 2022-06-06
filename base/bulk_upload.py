import os, EncryptedNAS

try: raw_input
except NameError: raw_input = input

file_tag_pairs = []
extensions = set()

print('Enter a path. The names of all paths within the path will be used as tags.')
path = raw_input('> ')
if path[0] == '"' and path[-1] == '"':
  path = path[1:-1]

for root, dirs, pfiles in os.walk(path):
  if root == path:
    continue
  tags = os.path.split(root)[1].split()
  files = []
  for file in pfiles:
    if file in ['Thumbs.db', 'desktop.ini']:
      continue
    fp = os.path.join(root, file)
    files.append(fp)
    extensions.add(os.path.splitext(fp)[1])
  file_tag_pairs.append((files,tags))
  print(files[0] + ' - ' + str(tags))

file_queue = []
for files, tags in file_tag_pairs:
  for file in files:
    file_queue.insert(0,(file,tags))

all_files = [i[0] for i in file_queue]

while True:
  print('\n\n\n')
  print('Types of files being uploaded: ' + ' '.join(map(str, extensions)))
  print('Number of files being uploaded: ' + str(len(all_files)))
  print('Number of file-tag pairs: ' + str(len(file_tag_pairs)))
  print('The above pairs will be used. Type "yes" to continue or quit to abort.')
  if raw_input('> ') == 'yes':
    break
  
new_tags = set()
all_tags = EncryptedNAS.get_all_tags()
for files, tags in file_tag_pairs:
  for tag in tags:
    if tag not in all_tags:
      new_tags.add(tag)
if len(new_tags) > 0:
  while True:
    print('\n\n\n')
    print('New tags being added: ' + ' '.join(new_tags))
    print('The above new tags will be used. Type "yes" to continue or quit to abort.')
    if raw_input('> ') == 'yes':
      break

file2hash = EncryptedNAS.hash_and_check_for_duplicates(all_files)

affected_bins = set()
chunk_num = 0
while len(file_queue) > 0:
  chunk_num += 1
  chunk_size = 0
  chunk_tags_files_pairs = {}
  while (chunk_size*EncryptedNAS.AVERAGE_COMPRESSION_RATIO) < EncryptedNAS.config['max_bin_size']:
    try: file, tags = file_queue.pop()
    except IndexError: break
    chunk_size += os.path.getsize(file)
    chunk_tags_files_pairs.setdefault(tuple(tags), []).append(file)
  print('Uploading chunk '+str(chunk_num)+'...')

  chunk_files = sum(chunk_tags_files_pairs.values(), [])
  bin_num, file2name = EncryptedNAS.upload_to_NAS(chunk_files)
  file2url = EncryptedNAS.upload_to_external_host(chunk_files)
  for tags, files in chunk_tags_files_pairs.items():    
    EncryptedNAS.update_database(files, tags, file2name, file2hash, bin_num, file2url)
  affected_bins.add(bin_num)

print('Verifying bins...')
for bin_num in affected_bins:
  EncryptedNAS.verify_bin(bin_num)

print('\n\nBulk upload complete!')
