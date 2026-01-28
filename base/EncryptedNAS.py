import sys, webbrowser, requests, json, random, time, tempfile, os, uuid, subprocess, shutil, hashlib, sqlite3, threading, mimetypes, socketserver
from requests_toolbelt import MultipartEncoder
import path_helper

AVERAGE_COMPRESSION_RATIO = 0.96

try:
  import BaseHTTPServer, Tkinter, tkFileDialog, tkSimpleDialog, tkMessageBox, urllib
except:
  import http.server as BaseHTTPServer
  import tkinter.filedialog as Tkinter
  import tkinter.simpledialog as tkSimpleDialog
  import tkinter.messagebox as tkMessageBox
  import urllib.parse as urllib

def byts(s): return bytes(s, sys.stdout.encoding)

with open('config.json', 'r') as f:
  config = json.loads(f.read())

LOCAL_CACHE_DIR = os.path.join(tempfile.gettempdir(), 'EncryptedNAS-Local-Cache')
CACHE_LOCK = threading.Lock()

DATABASE = sqlite3.connect(config['database'])

def human_readable_size(size):
  if size > (1024**3):
    return '%.3f GB' % (float(size)/(1024**3))
  if size > (1024**2):
    return '%.3f MB' % (float(size)/(1024**2))
  if size > (1024):
    return '%.3f KB' % (float(size)/(1024))
  return str(size) + ' B'

def hash_file(path):
  sha1 = hashlib.sha1()
  with open(path, 'rb') as f:
    while True:
      data = f.read(64*1024)
      if not data:
        break
      sha1.update(data)
  return sha1.hexdigest()

def try_manual_upload(fpath):
  name_found = False
  with open('manual_upload.txt', 'r') as f:
    for line in f:
      line = line.strip()
      if len(line) > 2:
        if name_found:
          return line
        elif fpath.endswith(line):
          name_found = True
  raise Exception('not found')

def upload_file(fpath):
  try: return try_manual_upload(fpath)
  except: pass

  hosts = []

  image_exts = ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.jfif', '.webp']

  random.shuffle(hosts)
  for host in hosts:
    try:
      return host(fpath)
    except Exception as ex:
      print('  Exception: ' + str(ex))

  # If all else fails, cache locally
  try: os.makedirs(LOCAL_CACHE_DIR)
  except OSError: pass
  cache_name = hash_file(fpath) + os.path.splitext(fpath)[1]
  if not hasattr(os, 'O_BINARY'): setattr(os, 'O_BINARY', 0)
  try:
    fd = os.open(os.path.join(LOCAL_CACHE_DIR, cache_name), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_BINARY)
    with os.fdopen(fd, 'wb') as f:
      with open(fpath, 'rb') as sf:
        shutil.copyfileobj(sf, f)
  except OSError:
    pass

  # return '/cache/' + cache_name
  return ''

def upload_to_external_host(files):
  file2url = {}
  for file in files:
    print('Uploading ' + file + ' to external host...')
    file2url[file] = upload_file(file)
  return file2url

class HaltUploadError(Exception): pass

def hash_and_check_for_duplicates(files):
  print('Hashing files and checking for duplicates...')
  hash2file, file2hash = {}, {}
  found_duplicates = False
  for file in files:
    hash = hash_file(file)
    if hash in hash2file:
      print('Error, duplicates: ' + file + '  ' + hash2file[hash])
      found_duplicates = True
    hash2file[hash] = file
    file2hash[file] = hash
  c = DATABASE.cursor()
  for file, hash in file2hash.items():
    c.execute('SELECT * FROM Files WHERE Hash = (?)', (hash,))
    r = c.fetchone()
    if r is not None:
      print('Error, duplicates: ' + file + '  ' + str(r))
      found_duplicates = True
  if found_duplicates:
    raise HaltUploadError()
  return file2hash

def get_bin_path():
  return os.path.join(path_helper.get_path(), config['path'])

_size_cache = {}
def getsize_cached(path):
  VALID_CACHE_TIME = 3*60
  try:
    size, t = _size_cache[path]
    if (time.time() - t) <= VALID_CACHE_TIME:
      return size
  except KeyError:
    pass
  size = os.path.getsize(path)
  t = time.time()
  _size_cache[path] = size, t
  return size

def invalidate_size_cache(path):
  try: _size_cache.pop(path)
  except KeyError: pass

def upload_to_NAS(files, ensure_unique_names=True):
  print('Finding bin number...')
  nas_path = get_bin_path()
  bin_num = 1
  bin_exists = False
  while True:
    bin_name = 'bin'+str(bin_num)+'.7z'
    rpath = os.path.join(nas_path, bin_name)
    try:
      if getsize_cached(rpath) >= config['max_bin_size']:
        bin_num += 1
      else:
        bin_exists = True
        break
    except FileNotFoundError:
      break

  tdir = tempfile.mkdtemp()
  tbin = os.path.join(tdir, bin_name)

  # If bin exists, get it
  if bin_exists:
    print('Downloading old bin from '+rpath+' ...')
    shutil.copy2(rpath, tbin)

  # Create unique names for files and rename
  file2name = {}
  for file in files:
    if ensure_unique_names:
      new_name = os.path.join(os.path.dirname(file),str(uuid.uuid4())+'_'+os.path.basename(file))
      os.rename(file, new_name)
    else:
      new_name = file
    file2name[file] = new_name

  # Create new bin or add to existing bin and update
  file_list = os.path.join(tdir, 'file_list.txt')
  with open(file_list, 'w', encoding='utf-8') as f:
    for file in file2name.values():
      f.write(file + '\n')
  # cmd = '"' + config['7z_path'] + '" a "' + tbin + '" -mhe -mx=9 -p' + config['password'] + ' -scsWIN @"'+file_list+'"'
  subprocess.check_call((config['7z_path'], 'a', tbin,
                         '-mhe',
                         '-mx=9',
                         '-p'+config['password'],
                         '-scsWIN',
                         '@'+file_list))

  # Restore old file names
  for old, new in file2name.items():
    os.rename(new, old)

  # Upload the bin
  print('\nBin Size: ' + human_readable_size(os.path.getsize(tbin)))
  shutil.copy2(tbin, rpath)
  invalidate_size_cache(rpath)

  shutil.rmtree(tdir)
  return bin_num, file2name

def update_database(files, tags, file2name, file2hash, bin_num, file2url):
  print('Updating database...')
  c = DATABASE.cursor()
  for file in files:
    name = os.path.basename(file2name[file])
    mtime = os.path.getmtime(file)
    c.execute('INSERT INTO Files VALUES (?,?,?,?,?,?)', (None, name, mtime, file2hash[file], bin_num, file2url[file]))
    c.execute('SELECT ID FROM Files WHERE Name=(?)', (name,))
    file_id = c.fetchone()[0]
    for tag in tags:
      try:
        c.execute('INSERT INTO Tags VALUES (?,?)', (None, tag,))
      except sqlite3.IntegrityError:
        pass
      c.execute('SELECT ID FROM Tags WHERE Name=(?)', (tag,))
      tag_id = c.fetchone()[0]
      c.execute('INSERT INTO File_Tags VALUES (?,?)', (file_id,tag_id))
  DATABASE.commit()

def raw_database_connection():
  return DATABASE

def verify_bin(bin_num):
  tdir = tempfile.mkdtemp()
  extract_bin_to_dir(bin_num, tdir)
  c = DATABASE.execute('SELECT Name, Hash FROM Files WHERE Bin = (?)', (bin_num,))
  db_files2hashes = {name:hash for name, hash in c.fetchall()}
  extracted_files = os.listdir(tdir)
  if len(extracted_files) != len(db_files2hashes):
    raise Exception('UNEXPECTED different number of files in database and extracted bin', bin_num)
  for name in extracted_files:
    if hash_file(os.path.join(tdir, name)) != db_files2hashes[name]:
      raise Exception('UNEXPECTED hash of extracted file does not match hash in database', bin_num, name)
  shutil.rmtree(tdir)

def get_all_tags():
  c = DATABASE.cursor()
  c.execute('SELECT Name FROM Tags')
  return [t[0] for t in c.fetchall()]

def get_tags_by_name(name, db=None):
  def impl(name, db):
    c = db.cursor()
    c.execute('SELECT t.Name FROM Files f, Tags t, File_Tags ft WHERE f.name = (?) AND f.ID = ft.File AND ft.Tag = t.ID', (name,))
    return [t[0] for t in c.fetchall()]
  
  if db:
    return impl(name, db)
  
  return impl(name, DATABASE)

def search_by_tags(tags, db=None):
  def impl(tags, db):
    c = db.cursor()
    t = '(' + ','.join(['?']*len(tags)) + ')'
    c.execute('SELECT f.* FROM Files f, Tags t, File_Tags ft WHERE ft.Tag = t.ID AND (t.Name IN '+t+') AND f.ID = ft.File GROUP BY f.ID HAVING COUNT(f.ID)='+str(len(tags)), tags)
    return c.fetchall()
  if db:
    return impl(tags, db)

  return impl(tags, DATABASE)

def add_tag(name, tag):
  c = DATABASE.execute('SELECT ID FROM Files WHERE Name=(?)', (name,))
  file_id = c.fetchone()[0]
  try:
    c.execute('INSERT INTO Tags VALUES (?,?)', (None, tag,))
  except sqlite3.IntegrityError:
    pass
  c.execute('SELECT ID FROM Tags WHERE Name=(?)', (tag,))
  tag_id = c.fetchone()[0]
  c.execute('INSERT INTO File_Tags VALUES (?,?)', (file_id,tag_id))
  DATABASE.commit()

def remove_tag(name, tag):
  c = DATABASE.cursor()
  c.execute('SELECT ID FROM Files WHERE Name=(?)', (name,))
  file_id = c.fetchone()[0]
  c.execute('SELECT ID FROM Tags WHERE Name=(?)', (tag,))
  tag_id = c.fetchone()[0]
  c.execute('DELETE FROM File_Tags WHERE File = (?) AND TAG = (?)', (file_id, tag_id))
  DATABASE.commit()

def extract_bin_to_dir(bin_num, out_dir):
  nas_path = get_bin_path()
  bin_name = 'bin'+str(bin_num)+'.7z'
  rpath = os.path.join(nas_path, bin_name)
  tbin = os.path.join(out_dir, bin_name)
  shutil.copy2(rpath, tbin)
  # cmd = cmd = '"' + config['7z_path'] + '" e "' + tbin + '" -o"' + out_dir + '" -p' + config['password']
  subprocess.check_call((config['7z_path'], 'e', tbin,
                         '-o' + out_dir,
                         '-p' + config['password']))
  os.remove(tbin)

def delete_file_from_database(name):
  cur = DATABASE.execute('BEGIN EXCLUSIVE')
  cur.execute('SELECT ID FROM Files WHERE Name = (?)', (name,))
  id, = cur.fetchone()
  cur.execute('DELETE FROM Files WHERE Name = (?) AND ID = (?)', (name, id))
  cur.execute('DELETE FROM File_Tags WHERE File = (?)', (id,))
  db.commit()

class Handler(BaseHTTPServer.BaseHTTPRequestHandler):
  def send_json(s, data):
    s.send_response(200)
    s.send_header('Content-type', 'application/json')
    s.end_headers()
    s.wfile.write(byts(json.dumps(data)))

  def _get_range(s, size):
    h = s.headers.get('Range')
    if h is None:
      return 0, size-1
    h = h.split('=')[1]
    start, end = s.headers.get('Range').strip().strip('bytes=').split('-')
    start = max(0, int(start) if start else 0)
    end = min(int(end) if end else size-1, size-1)
    return start, end

  def do_GET(s):
    spath = s.path.split('/')
  
    # Load app
    if s.path == '/app':
      s.send_response(200)
      s.send_header('Content-type', 'text/html')
      s.end_headers()
      with open('app.htm', 'rb') as af:
        s.wfile.write(af.read())
    
    # All Tags
    if s.path == '/tags':
      s.send_json(get_all_tags())

    # Tags for a Specific File
    if len(spath) == 4 and spath[1] == 'files' and spath[3] == 'tags':
      f = (urllib.unquote(spath[2]),)
      tags = get_tags_by_name(f)
      s.send_json(tags)

    # Search by tags      
    if s.path.startswith('/tags/'):
      tags = spath[2:]
      s.send_json(search_by_tags(tags))

    # Random Pics
    if s.path == '/random':
      with sqlite3.connect(config['database']) as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM Files WHERE ID IN (SELECT ID FROM Files ORDER BY RANDOM() LIMIT 100)')
        r = c.fetchall()
      s.send_json(r)

    # Load from Cache
    if len(spath) == 3 and spath[1] == 'cache':
      cache_path = os.path.join(LOCAL_CACHE_DIR, spath[2])
      content_type, _ = mimetypes.guess_type(cache_path)
      with CACHE_LOCK:
        for _ in range(2):
          try:
            with open(cache_path, 'rb') as f:
              size = os.fstat(f.fileno()).st_size
              start, end = s._get_range(size)
              is_partial = 'Range' in s.headers
              s.send_response(206 if is_partial else 200)
              s.send_header('Accept-Ranges', 'bytes')
              s.send_header('Content-Length', str(end-start+1))
              if is_partial:
                s.send_header('Content-Range', 'bytes %s-%s/%s' % (start, end, size))
              s.send_header('Content-Type', content_type)
              s.end_headers()
              remaining = end - start + 1
              f.seek(start)
              while remaining > 0:
                buf = f.read(remaining)
                remaining -= len(buf)
                try:
                  s.wfile.write(buf)
                except (ConnectionResetError, ConnectionAbortedError):
                  return
            break
          except FileNotFoundError:
            hash, _ = os.path.splitext(spath[2])
            with sqlite3.connect(config['database']) as conn:
              c = conn.execute('SELECT Bin FROM Files WHERE Hash = ?', (hash,))
              bin_num, = c.fetchone()
            tdir = tempfile.mkdtemp()
            print('Downloading bin', bin_num, 'to', tdir)
            extract_bin_to_dir(bin_num, tdir)
            try: os.makedirs(LOCAL_CACHE_DIR)
            except OSError: pass
            for file in os.listdir(tdir):
              _, ext = os.path.splitext(file)
              full_path = os.path.join(tdir, file)
              hash = hash_file(full_path)
              cpath = os.path.join(LOCAL_CACHE_DIR, hash + ext)
              try:
                os.rename(full_path, cpath)
              except FileExistsError:
                pass
            shutil.rmtree(tdir)

  def do_PUT(s):
    # Add files
    if s.path == '/files':
      s.send_response(204)
      s.end_headers()
      win = Tkinter.Tk()
      win.attributes('-alpha', 0.0001)
      if s.server.upload_in_progress:
        tkMessageBox.showerror('Upload in Progress', 'An upload is still running.\nPlease wait for it to finish.')
        win.destroy()
        return
      s.server.upload_in_progress = True
      files = tkFileDialog.askopenfilenames(title='Choose file(s) to add')
      if len(files) < 1:
        s.server.upload_in_progress = False
        win.destroy()
        return
      tags = tkSimpleDialog.askstring('Tags','Please enter tags separated by spaces:')
      while tags is not None and len(tags) < 1:
        tags = tkSimpleDialog.askstring('Tags','Please enter at least one tag.\nEnter tags separated by spaces:')
      if tags is None:
        s.server.upload_in_progress = False
        win.destroy()
        return
      tags = tags.split()
      tkMessageBox.showinfo('Add file(s)', 'Your file(s) are uploading in the background.\nCheck the console for progress.')
      win.destroy()
      
      try:
        file2hash = hash_and_check_for_duplicates(files)
        bin_num, file2name = upload_to_NAS(files)
        file2url = upload_to_external_host(files)
        update_database(files, tags, file2name, file2hash, bin_num, file2url)
      except HaltUploadError as e:
        s.server.upload_in_progress = False
        raise e
      
      print('Upload Complete!')
      s.server.upload_in_progress = False
      
  def do_POST(s):
    spath = s.path.split('/')
    
    # Add Tag to File
    if len(spath) == 5 and spath[1] == 'files' and spath[3] == 'tags':
      name = urllib.unquote(spath[2])
      tag = urllib.unquote(spath[4])
      add_tag(name, tag)
      s.send_json("OK")

  def do_DELETE(s):
    spath = s.path.split('/')

    # Remove Tag from File
    if len(spath) == 5 and spath[1] == 'files' and spath[3] == 'tags':
      name = urllib.unquote(spath[2])
      tag = urllib.unquote(spath[4])
      remove_tag(name, tag)
      s.send_json("OK")

class Server(socketserver.ThreadingMixIn, BaseHTTPServer.HTTPServer):
  upload_in_progress = False

def main():
  port = config['port']
  app_url = 'http://localhost:'+str(port)+'/app'
  print('App running at ' + app_url)
  webbrowser.open(app_url)
  httpd = Server(('', port), Handler)
  try:
    httpd.serve_forever()
  except KeyboardInterrupt:
    pass
  httpd.server_close()

if __name__ == '__main__': main()
