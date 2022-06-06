import sessen, sys, json, tempfile, os, subprocess, shutil, hashlib, threading, mimetypes, urllib.parse, time, queue, tempfile
import multithreaded_sqlite
config = json.loads(sessen.get_file('config.json'))

nas_helper = sessen.ExtensionProxy('nas_helper')
logger = sessen.getLogger()

LOCAL_CACHE_DIR = os.path.join(tempfile.gettempdir(), 'EncryptedNAS-Local-Cache')
CACHE_LOCK = threading.Lock()

app_html = sessen.get_file('app.htm')
persistent = sessen.PersistentDatastore()

db_connection = multithreaded_sqlite.connect(config['database'])

def hash_file(path):
  sha1 = hashlib.sha1()
  with open(path, 'rb') as f:
    while True:
      data = f.read(64*1024)
      if not data:
        break
      sha1.update(data)
  return sha1.hexdigest()

def _get_range(connection, size):
  try:
    start, end = connection.request_headers['Range'][0].strip().strip('bytes=').split('-')
  except KeyError:
    return 0, size-1
  start = max(0, int(start) if start else 0)
  end = min(int(end) if end else size-1, size-1)
  return start, end

@sessen.bind('GET', '/?$')
def app(connection):
  connection.send_html(app_html)

def requires_login(func):
  def wrapper(connection, *args, **kwargs):
    try:
      logged_in = persistent.get(connection, 'logged_in')
      if logged_in:
        return func(connection, *args, **kwargs)
    except KeyError:
      pass
    connection.send_json({'error': 'Unauthenticated'})
  return wrapper

@sessen.bind('GET', '/tags$')
@requires_login
def get_all_tags(connection):
  def f(database):
    c = database.execute('SELECT Name FROM Tags')
    tags = [t[0] for t in c.fetchall()]
    return tags
  tags = db_connection.run(f)
  connection.send_json(tags)

@sessen.bind('GET', '/files/(?P<filename>.+?)/tags$')
@requires_login
def get_file_tags(connection):
  fname = urllib.parse.unquote(connection.args['filename'])
  def f(database):
    c = database.execute('SELECT t.Name FROM Files f, Tags t, File_Tags ft WHERE f.name = (?) AND f.ID = ft.File AND ft.Tag = t.ID', (fname,))
    return [t[0] for t in c.fetchall()]
  tags = db_connection.run(f)
  connection.send_json(tags)

@sessen.bind('GET', '/tags/(.+?/)*.+?$')
@requires_login
def search_by_tags(connection):
  tags = connection.path[6:].split('/')
  def f(database):
    t = '(' + ','.join(['?']*len(tags)) + ')'
    query = 'SELECT f.* FROM Files f, Tags t, File_Tags ft WHERE ft.Tag = t.ID AND (t.Name IN '+t+') AND f.ID = ft.File GROUP BY f.ID HAVING COUNT(f.ID)='+str(len(tags))
    c = database.execute(query, tags)
    return c.fetchall()
  connection.send_json(db_connection.run(f))

@sessen.bind('GET', '/random$')
@requires_login
def random_pics(connection):
  def f(database):
    c = database.execute('SELECT * FROM Files WHERE ID IN (SELECT ID FROM Files ORDER BY RANDOM() LIMIT 100)')
    return c.fetchall()
  connection.send_json(db_connection.run(f))

def get_bin_containing_hash(hash):
  def f(database):
    c = database.execute('SELECT Bin FROM Files WHERE Hash = ?', (hash,))
    bin_num, = c.fetchone()
    return bin_num
  return db_connection.run(f)

def _send_from_cache(connection):
  fname = connection.args['filename']
  cache_path = os.path.join(LOCAL_CACHE_DIR, fname)
  content_type, _ = mimetypes.guess_type(cache_path)
  with open(cache_path, 'rb') as f:
    size = os.fstat(f.fileno()).st_size
    start, end = _get_range(connection, size)
    is_partial = 'Range' in connection.request_headers
    connection.set_response_code(206 if is_partial else 200)
    connection.add_header('Accept-Ranges', 'bytes')
    connection.add_header('Content-Length', str(end-start+1))
    if is_partial:
      connection.add_header('Content-Range', 'bytes %s-%s/%s' % (start, end, size))
    connection.add_header('Content-Type', content_type)
    remaining = end - start + 1
    f.seek(start)
    while remaining > 0:
      buf = f.read(min(remaining, 5*1024*1024))
      remaining -= len(buf)
      try:
        connection.write(buf)
      except (ConnectionResetError, ConnectionAbortedError):
        return

def _download_from_nas(connection):
  fname = connection.args['filename']
  hash, _ = os.path.splitext(fname)
  bin_num = get_bin_containing_hash(hash)
  bin_name = 'bin'+str(bin_num)+'.7z'
  rpath = os.path.join(config['path'], bin_name)
  try:
    tdir = tempfile.mkdtemp()
  except:
    breakpoint()
  tbin = os.path.join(tdir, bin_name)
  logger.info('Downloading bin to ' + tbin)
  nas_helper.copy(rpath, tbin)
  cmd = '"' + config['7z_path'] + '" e "' + tbin + '" -o"' + tdir + '" -p' + config['password']
  proc = subprocess.run(cmd, capture_output=True)
  if proc.returncode:
    logger.critical('7z STDOUT: ' + proc.stdout.decode())
    logger.critical('7z STDERR: ' + proc.stderr.decode())
    proc.check_returncode()
  os.remove(tbin)
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

@sessen.bind('GET', r'/cache/(?P<filename>[^\?]+)(\?.+)?')
@requires_login
def load_from_cache(connection):
  try:
    _send_from_cache(connection)
  except FileNotFoundError:
    with CACHE_LOCK:
      try:
        _send_from_cache(connection)
      except FileNotFoundError:
        _download_from_nas(connection)
    _send_from_cache(connection)

@sessen.bind('POST', '/files/(?P<filename>.+?)/tags/(?P<tag>.+?)$')
@requires_login
def add_tag_to_file(connection):
  fname = urllib.parse.unquote(connection.args['filename'])
  tag = connection.args['tag']
  def f(database):
    c = database.execute('SELECT ID FROM Files WHERE Name=(?)', (fname,))
    file_id = c.fetchone()[0]
    c.execute('INSERT OR IGNORE INTO Tags VALUES (?,?)', (None, tag,))
    c.execute('SELECT ID FROM Tags WHERE Name=(?)', (tag,))
    tag_id = c.fetchone()[0]
    c.execute('INSERT OR IGNORE INTO File_Tags VALUES (?,?)', (file_id,tag_id))
    database.commit()
  db_connection.run(f)
  connection.send_json("OK")

@sessen.bind('DELETE', '/files/(?P<filename>.+?)/tags/(?P<tag>.+?)$')
@requires_login
def remove_tag_from_file(connection):
  fname = urllib.parse.unquote(connection.args['filename'])
  tag = connection.args['tag']
  def f(database):
    c = database.execute('SELECT ID FROM Files WHERE Name=(?)', (fname,))
    file_id = c.fetchone()[0]
    c.execute('SELECT ID FROM Tags WHERE Name=(?)', (tag,))
    tag_id = c.fetchone()[0]
    c.execute('DELETE FROM File_Tags WHERE File = (?) AND TAG = (?)', (file_id, tag_id))
    database.commit()
  db_connection.run(f)
  connection.send_json("OK")

@sessen.bind('POST', '/login$')
def login(connection):
  try:
    password = connection.receive_json()['password']
    if password == config['password']:
      persistent.set(connection, 'logged_in', True)
      connection.send_json({'error':None})
      return
  except:
    pass
  connection.send_json({'error': 'Invalid Login'})

@sessen.bind('POST', '/logout$')
def logout(connection):
  persistent.delete_all(connection)
  connection.send_json({'error':None})
