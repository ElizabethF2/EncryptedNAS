import os, tempfile, shutil, EncryptedNAS

try: raw_input
except NameError: raw_input = input

def split_bin(old_bin_num):
  old_bin_dir = os.path.join(EncryptedNAS.get_bin_path(), 'bin'+str(old_bin_num)+'.7z')
  if os.path.getsize(old_bin_dir) <= EncryptedNAS.config['max_bin_size']:
    print('Bin already under the max size, nothing to split')
    return

  tdir = tempfile.mkdtemp()
  print('Created temp dir', tdir)

  print('Downloading bin...')
  EncryptedNAS.extract_bin_to_dir(old_bin_num, tdir)

  print('Splitting...')
  remaining_files = [os.path.join(tdir, f) for f in os.listdir(tdir)]
  chunk_num = 0
  while len(remaining_files) > 0:
    chunk_num += 1
    chunk_size = 0
    chunk_files = []
    while (chunk_size*EncryptedNAS.AVERAGE_COMPRESSION_RATIO) < EncryptedNAS.config['max_bin_size']:
      try: file = remaining_files.pop()
      except IndexError: break
      fsize = os.path.getsize(file)
      if len(chunk_files) > 0 and (chunk_size*EncryptedNAS.AVERAGE_COMPRESSION_RATIO) > EncryptedNAS.config['max_bin_size']:
        break
      chunk_size += fsize
      chunk_files.append(file)

    print('Verify hashes for chunk...')
    with EncryptedNAS.raw_database_connection() as db:
      c = db.cursor()
      for file in chunk_files:
        c.execute('SELECT Hash FROM Files WHERE Name = (?)', (os.path.basename(file),))
        db_hash, = c.fetchone()
        if db_hash != EncryptedNAS.hash_file(file):
          raise Exception('UNEXPECTED Hash of extracted file does not match hash in database', file)

    print('Compressing and uploading chunk '+str(chunk_num)+'...')
    bin_num, _ = EncryptedNAS.upload_to_NAS(chunk_files, ensure_unique_names=False)

    print('Updating bin number in database...')
    with EncryptedNAS.raw_database_connection() as db:
      c = db.cursor()
      for file in chunk_files:
        c.execute('UPDATE Files SET Bin = (?) WHERE Name = (?)', (bin_num, os.path.basename(file)))

  print('Verifying no files remain with the old bin number...')
  with EncryptedNAS.raw_database_connection() as db:
    c = db.cursor()
    c.execute('SELECT * FROM Files WHERE Bin = (?)', (old_bin_num,))
    res = list(c.fetchall())
    if len(res) > 0:
      raise Exception('UNEXPECTED some files still in old bin', old_bin_num)

  print('Delete the old bin')
  os.remove(old_bin_dir)

  print('Delete temp dir')
  shutil.rmtree(tdir)

def fix_files_with_wrong_bins(first_bin_num, last_bin_num):
  bin_num = first_bin_num
  while bin_num <= last_bin_num:
    print('On Bin:', bin_num)

    tdir = tempfile.mkdtemp()
    print('Created temp dir', tdir)

    print('Downloading bin...')
    EncryptedNAS.extract_bin_to_dir(bin_num, tdir)

    print('Update db...')
    with EncryptedNAS.raw_database_connection() as db:
      c = db.cursor()
      for file in os.listdir(tdir):
        c.execute('UPDATE Files SET Bin = (?) WHERE Name = (?)', (bin_num, file))
    
    print('Delete temp dir')
    shutil.rmtree(tdir)

    bin_num += 1

def verify_and_delete_old_bin(bin_num):
  tdir = tempfile.mkdtemp()
  print('Created temp dir', tdir)

  print('Downloading bin...')
  EncryptedNAS.extract_bin_to_dir(bin_num, tdir)

  with EncryptedNAS.raw_database_connection() as db:
    c = db.cursor()
    for file in os.listdir(tdir):
      c.execute('SELECT Bin FROM Files WHERE Name = (?)', (file,))
      bin, = c.fetchone()
      if bin == bin_num:
        raise Exception('UNEXPECTED file not moved to new bin')
  
  print('Delete bin')
  os.remove(os.path.join(EncryptedNAS.get_bin_path(), 'bin'+str(bin_num)+'.7z'))
  
  print('Delete temp dir')
  shutil.rmtree(tdir)

if __name__ == '__main__':
  print('Enter the number of a bin to split:')
  old_bin_num = raw_input('> ').strip()
  split_bin(old_bin_num)

  print('Done!')
