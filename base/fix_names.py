import os, re

rx = r'^[\da-f]{8}(-[\da-f]{4}){4}[\da-f]{8}_'

paths = []

while True:
  p = input()
  if p == '':
    break
  paths.append(p)

for path in paths:
  for file in os.listdir(path):
    if re.match(rx, file):
      ofile = os.path.join(path, file)
      nfile = os.path.join(path, re.sub(rx, '', file))
      os.rename(ofile, nfile)
