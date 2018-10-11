# -*- coding: utf-8 -*-
# RSScrawler
# Projekt von https://github.com/rix1337
# Enthält Code von:
# https://github.com/bharnett/Infringer/blob/master/LinkRetrieve.py


import base64
import logging
import os
import re
import socket
import sys

import six

from rsscrawler.rssconfig import RssConfig
from rsscrawler.rssdb import ListDb
from rsscrawler.rssdb import RssDb

log_info = logging.info
log_error = logging.error
log_debug = logging.debug


def write_crawljob_file(package_name, folder_name, link_text, crawljob_dir, subdir, configfile):
    try:
        crawljob_file = crawljob_dir + '/%s.crawljob' % unicode(
            re.sub(r'[^\w\s\.-]', '', package_name.replace(' ', '')).strip().lower())
    except NameError:
        crawljob_file = crawljob_dir + '/%s.crawljob' % (
            re.sub(r'[^\w\s\.-]', '', package_name.replace(' ', '')).strip().lower())

    crawljobs = RssConfig('Crawljobs', configfile)
    autostart = crawljobs.get("autostart")
    usesubdir = crawljobs.get("subdir")
    if not usesubdir:
        subdir = ""
    if autostart:
        autostart = "TRUE"
    else:
        autostart = "FALSE"
    try:
        file = open(crawljob_file, 'w')
        file.write('enabled=TRUE\n')
        file.write('autoStart=' + autostart + '\n')
        file.write(
            'extractPasswords=["' + decode_base64("bW92aWUtYmxvZy5vcmc=") + '","' + decode_base64(
                "c2VyaWVuanVua2llcy5vcmc=") + '","' +
            decode_base64("aGQtYXJlYS5vcmc=") + '","' + decode_base64("aGQtd29ybGQub3Jn") + '","' + decode_base64(
                "d2FyZXotd29ybGQub3Jn") + '"]\n')
        file.write('downloadPassword=' +
                   decode_base64("c2VyaWVuanVua2llcy5vcmc=") + '\n')          

        file.write('extractAfterDownload=TRUE\n')
        file.write('forcedStart=' + autostart + '\n')
        file.write('autoConfirm=' + autostart + '\n')
        if not subdir == "":
            file.write('downloadFolder=' + subdir + "/" + '%s\n' % folder_name)
            if subdir == "RSScrawler/Remux":
                file.write('priority=Lower\n')
        else:
            file.write('downloadFolder=' + '%s\n' % folder_name)
        file.write('packageName=%s\n' % package_name.replace(' ', ''))
        file.write('text=%s\n' % link_text)
        file.close()
        return True
    except UnicodeEncodeError as e:
        log_error("Beim Schreibversuch des Crawljobs: %s FEHLER: %s" %
                  (crawljob_file, e.message))
        if os.path.isfile(crawljob_file):
            log_info("Entferne defekten Crawljob: %s" % crawljob_file)
            os.remove(crawljob_file)
        return False


def checkIp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 0))
        IP = s.getsockname()[0]
    except:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP


def entfernen(retailtitel, identifier):
    titles = retail_sub(retailtitel)
    retail = titles[0]
    retailyear = titles[1]
    if identifier == '2':
        liste = "MB_3D"
    else:
        liste = "MB_Filme"
    cont = ListDb(os.path.join(os.path.dirname(sys.argv[0]), "RSScrawler.db"), liste).retrieve()
    new_cont = []
    if cont:
        for line in cont:
            if line.lower() == retailyear.lower() or line.lower() == retail.lower():
                line = re.sub(r'^(' + re.escape(retailyear.lower()) + '|' + re.escape(retail.lower()) + ')', '',
                              line.lower())
            if line:
                new_cont.append(line)
    ListDb(os.path.join(os.path.dirname(sys.argv[0]), "RSScrawler.db"), liste).store_list(new_cont)
    RssDb(os.path.join(os.path.dirname(sys.argv[0]), "RSScrawler.db"), "retail").store(retail, "retail")
    RssDb(os.path.join(os.path.dirname(sys.argv[0]), "RSScrawler.db"), "retail").store(retailyear, "retail")
    log_debug(retail + " durch Cutoff aus " + liste + " entfernt.")


def retail_sub(title):
    simplified = title.replace(".", " ")
    retail = re.sub(
        r'(|.UNRATED.*|.Unrated.*|.Uncut.*|.UNCUT.*)(|.Directors.Cut.*|.Final.Cut.*|.DC.*|.EXTENDED.*|.Extended.*|.Theatrical.*|.THEATRICAL.*)(|.3D.*|.3D.HSBS.*|.3D.HOU.*|.HSBS.*|.HOU.*)(|.)\d{4}(|.)(|.UNRATED.*|.Unrated.*|.Uncut.*|.UNCUT.*)(|.Directors.Cut.*|.Final.Cut.*|.DC.*|.EXTENDED.*|.Extended.*|.Theatrical.*|.THEATRICAL.*)(|.3D.*|.3D.HSBS.*|.3D.HOU.*|.HSBS.*|.HOU.*).(German|GERMAN)(|.AC3|.DTS|.DTS-HD)(|.DL)(|.AC3|.DTS).(2160|1080|720)p.(UHD.|Ultra.HD.|)(HDDVD|BluRay)(|.HDR)(|.AVC|.AVC.REMUX|.x264|.x265)(|.REPACK|.RERiP|.REAL.RERiP)-.*',
        "", simplified)
    retailyear = re.sub(
        r'(|.UNRATED.*|.Unrated.*|.Uncut.*|.UNCUT.*)(|.Directors.Cut.*|.Final.Cut.*|.DC.*|.EXTENDED.*|.Extended.*|.Theatrical.*|.THEATRICAL.*)(|.3D.*|.3D.HSBS.*|.3D.HOU.*|.HSBS.*|.HOU.*).(German|GERMAN)(|.AC3|.DTS|.DTS-HD)(|.DL)(|.AC3|.DTS|.DTS-HD).(2160|1080|720)p.(UHD.|Ultra.HD.|)(HDDVD|BluRay)(|.HDR)(|.AVC|.AVC.REMUX|.x264|.x265)(|.REPACK|.RERiP|.REAL.RERiP)-.*',
        "", simplified)
    return retail, retailyear


def cutoff(key, identifier):
    retailfinder = re.search(
        r'(|.UNRATED.*|.Unrated.*|.Uncut.*|.UNCUT.*)(|.Directors.Cut.*|.Final.Cut.*|.DC.*|.EXTENDED.*|.Extended.*|.Theatrical.*|.THEATRICAL.*)(|.3D.*|.3D.HSBS.*|.3D.HOU.*|.HSBS.*|.HOU.*).(German|GERMAN)(|.AC3|.DTS|.DTS-HD)(|.DL)(|.AC3|.DTS|.DTS-HD).(2160|1080|720)p.(UHD.|Ultra.HD.|)(HDDVD|BluRay)(|.HDR)(|.AVC|.AVC.REMUX|.x264|.x265)(|.REPACK|.RERiP|.REAL.RERiP)-.*',
        key)
    if retailfinder:
        entfernen(key, identifier)
        return True
    else:
        return False

def decode_base64(value):
    if six.PY2:
        return value.decode("base64")
    else:
        return base64.b64decode(value).decode()
