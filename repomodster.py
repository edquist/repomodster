#!/usr/bin/python

# oh hai!

# let's have some fun with repomd.xml's ...

import os
import re
import sys
import bz2
import time
import getopt
import urllib2
import sqlite3

try:
    import xml.etree.ElementTree as et
except ImportError:  # if sys.version_info[0:2] == (2,4):
    import elementtree.ElementTree as et

def usage(status=0):
    script = os.path.basename(__file__)
    print "usage: %s [-ubsScdOC567] [-o ser] [-r repo] PACKAGE [...]" % script
    print
    print "each PACKAGE can be a full package name or contain '%' wildcards"
    print
    print "Options:"
    print "  -u   print full download urls"
    print "  -b   match binary packages (default=%s)" % what
    print "  -s   print source package name too"
    print "  -S   match source package names for binary package list"
    print "  -c   always use cached primary db (don't attempt to update)"
    print "  -d   download matching rpm(s)"
    print "  -O   use OSG repos (use with -o/-r)"
    print "  -C   use Centos repos"
    print "  -5,-6,-7   specify EL release series (default=%d)" % default_epel
    print
    print "  -o series  use osg series (3.2, 3.3, upcoming)"
    print "  -r repo    use osg repo (development, testing, release)"
    sys.exit(status)

default_epel = 6
epels = []
what = 'SRPMS'
printurl = False
printspkg = False
matchspkg = False
autoupdate = True
downloadrpms = False
stale_cache_age = 3600   # seconds
reposet = 'epel'
osgser = '3.2'
osgrepo = 'release'

try:
    ops,pkg_names = getopt.getopt(sys.argv[1:], 'ubsScdOC567r:o:')
except getopt.GetoptError:
    usage()

for op,val in ops:
    if   op == '-u': printurl = True
    elif op == '-b': what = 'x86_64'
    elif op == '-s': printspkg = True
    elif op == '-S': matchspkg = True
    elif op == '-c': autoupdate = False
    elif op == '-d': downloadrpms = True
    elif op == '-O': reposet = 'osg'
    elif op == '-C': reposet = 'centos'
    elif op == '-r': osgrepo = val
    elif op == '-o': osgser = val
    else           : epels += [int(op[1:])]

if len(epels) == 0:
    epels += [default_epel]

class Container:
    pass

def get_osg_info(el, what):
    info = Container()

    info.what    = 'source/SRPMS' if what == 'SRPMS' else what
    info.baseurl = 'http://repo.grid.iu.edu/osg/%s/el%d/%s/%s' % (
                     osgser, el, osgrepo, info.what)

    info.repomd  = info.baseurl + '/repodata/repomd.xml'

    cachename      = "osg-%s-el%d.%s" % (osgser, el, what)
    info.cachedir  = os.getenv('HOME') + "/.cache/epeldb"
    info.cachets   = info.cachedir + "/primary.%s.ts" % cachename
    info.cachedb   = info.cachedir + "/primary.%s.db" % cachename
    return info

def get_centos_info(el, what):
    info = Container()

    info.baseurl = 'http://vault.centos.org/%d.0/os/%s' % (el, what)
    info.repomd  = info.baseurl + '/repodata/repomd.xml'

    info.cachedir  = os.getenv('HOME') + "/.cache/epeldb"
    info.cachets   = info.cachedir + "/primary.centos%d.%s.ts" % (el, what)
    info.cachedb   = info.cachedir + "/primary.centos%d.%s.db" % (el, what)
    return info

def get_epel_info(el, what):
    info = Container()

    # mirror!
    info.baseurl = 'http://mirror.batlab.org/pub/linux/epel/%d/%s' % (el, what)
    #info.baseurl = 'http://ftp.osuosl.org/pub/fedora-epel/%d/%s' % (el, what)
    #info.baseurl = 'http://dl.fedoraproject.org/pub/epel/%d/%s' % (el, what)
    info.repomd  = info.baseurl + '/repodata/repomd.xml'

    info.cachedir  = os.getenv('HOME') + "/.cache/epeldb"
    info.cachets   = info.cachedir + "/primary.epel%d.%s.ts" % (el, what)
    info.cachedb   = info.cachedir + "/primary.epel%d.%s.db" % (el, what)
    return info

def get_reposet_info(el, what):
    if reposet == 'epel':
        getinfo = get_epel_info
    elif reposet == 'osg':
        getinfo = get_osg_info
    elif reposet == 'centos':
        getinfo = get_centos_info
    else:
        fail("herp derp, what's %s?" % reposet)
    return getinfo(el, what)

def msg(m=""):
    if sys.stderr.isatty():
        sys.stderr.write("\r%s\x1b[K" % m)

def fail(m="",status=1):
    print >>sys.stderr, m
    sys.exit(status)

def get_repomd_xml(info):
    handle = urllib2.urlopen(info.repomd)
    xml = handle.read()
    # strip xmlns garbage to simplify extracting things...
    xml = re.sub(r'<repomd [^>]*>', '<repomd>', xml)
    xmltree = et.fromstring(xml)
    return xmltree

def is_pdb(x):
    return x.get('type') == 'primary_db'

def cache_exists(info):
    return os.path.exists(info.cachets) and os.path.exists(info.cachedb)

def cache_is_recent(info):
    # if the cache is < 1h old, don't bother to see if there's a newer one
    return cache_exists(info) and \
           os.stat(info.cachets).st_mtime + stale_cache_age > time.time()

def xz_decompress(dat):
    from subprocess import Popen, PIPE
    return Popen(['xz','-d'], stdin=PIPE, stdout=PIPE).communicate(dat)[0]

def update_cache(info):
    msg("fetching latest repomd.xml...")
    tree = get_repomd_xml(info)
    msg()
    datas = tree.findall('data')
    primary = filter(is_pdb, datas)[0]
    primary_href = primary.find('location').get('href')
    primary_url = info.baseurl + '/' + primary_href
    primary_ts = float(primary.find('timestamp').text)  # hey let's use this...

    if not os.path.exists(info.cachedir):
        os.makedirs(info.cachedir)
    if cache_exists(info):
        last_ts = float(open(info.cachets).readline().strip())
    else:
        last_ts = 0

    if primary_ts > last_ts:
        msg("fetching latest primary db...")
        primary_zip = urllib2.urlopen(primary_url).read()
        msg("decompressing...")
        if primary_url.endswith('.xz'):
            primary_db = xz_decompress(primary_zip)
        else:
            primary_db = bz2.decompress(primary_zip)
        msg("saving cache...")
        open(info.cachedb, "w").write(primary_db)
        print >>open(info.cachets, "w"), primary_ts
        msg()
    else:
        # touch ts file to mark as recent
        os.utime(info.cachets, None)

def do_cache_setup(info):
    if not autoupdate:
        if cache_exists(info):
            return
        else:
            fail("cache requested but does not exist...")
    if not cache_is_recent(info):
        try:
            update_cache(info)
        except urllib2.URLError:
            msg()
            if not cache_exists(info):
                fail("primary db cache does not exist and download failed...")

def download(url):
    dest = url.split('/')[-1]
    handle = urllib2.urlopen(url)
    msg("downloading %s..." % dest)
    open(dest, "w").write(handle.read())
    msg()

def getsql():
    match = 'spkg' if matchspkg else 'name'

    def like(name):
        return match + " %s ?" % ("like" if "%" in name else "=")

    if '%' in ''.join(pkg_names) or len(pkg_names) == 1:
        nameclause = ' or '.join(map(like,pkg_names))
    else:
        nameclause = match + " in (" + ','.join('?' for x in pkg_names) + ")"

    select  = "select location_href, vrstrip(rpm_sourcerpm) spkg from packages"
    where   = "where (%s) and arch not in ('i386','i686')" % nameclause 
    if printspkg:
        orderby = "order by rpm_sourcerpm, name, version, release, arch"
    else:
        orderby = "order by name, version, release, arch"
    return ' '.join([select, where, orderby])


def regexp(rx,s):
    return re.search(rx,s) is not None

def vrstrip(s):
    if s is not None:
        return s.rsplit('-',2)[0]

def main():
    if not pkg_names:
        usage()

    for info in ( get_reposet_info(epel, what) for epel in epels ):
        run_for_repo(info)

def run_for_repo(info):
    do_cache_setup(info)

    db = sqlite3.connect(info.cachedb)
    # db.create_function("regexp", 2, regexp)
    db.create_function("vrstrip", 1, vrstrip)
    c  = db.cursor()

    sql = getsql()
    c.execute(sql, pkg_names)

    for href,spkg in c:
        if printspkg and what == 'x86_64':
            print "[%s]" % spkg,
        if printurl:
            print info.baseurl + "/" + href
        else:
            print href.split('/')[-1]
        if downloadrpms:
            download(info.baseurl + "/" + href)

if __name__ == '__main__':
    main()

