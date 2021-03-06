# -*- coding: utf-8 -*-
# RSScrawler
# Projekt von https://github.com/rix1337

import base64
import datetime
import logging
import re
import socket

from rsscrawler import myjdapi
from rsscrawler.config import RssConfig
from rsscrawler.db import ListDb
from rsscrawler.db import RssDb

log_info = logging.info
log_error = logging.error
log_debug = logging.debug


class Unbuffered(object):
    def __init__(self, stream):
        self.stream = stream

    def write(self, data):
        self.stream.write(data)
        self.stream.flush()

    def writelines(self, datas):
        self.stream.writelines(datas)
        self.stream.flush()

    def __getattr__(self, attr):
        return getattr(self.stream, attr)


def add_decrypt(title, link, password, dbfile):
    try:
        RssDb(dbfile, 'to_decrypt').store(title, link + '|' + password)
        return True
    except:
        return False


def check_hoster(to_check, configfile):
    hosters = RssConfig("Hosters", configfile).get_section()
    for hoster in hosters:
        if hosters[hoster] == "True":
            if hoster in to_check.lower() or to_check.lower() in hoster:
                return True
    return False


def check_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 0))
        ip = s.getsockname()[0]
    except:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip


def check_is_site(string, configfile):
    hostnames = RssConfig('Hostnames', configfile)
    sj = hostnames.get('sj')
    dj = hostnames.get('dj')
    sf = hostnames.get('sf')
    mb = hostnames.get('mb')
    hw = hostnames.get('hw')
    hs = hostnames.get('hs')
    fx = hostnames.get('fx')
    nk = hostnames.get('nk')
    dd = hostnames.get('dd')
    fc = hostnames.get('fc')

    if sj and sj.split('.')[0] in string:
        return "SJ"
    elif dj and dj.split('.')[0] in string:
        return "DJ"
    elif sf and sf.split('.')[0] in string:
        return "SF"
    elif mb and mb.split('.')[0] in string:
        return "MB"
    elif hw and hw.split('.')[0] in string:
        return "HW"
    elif fx and fx.split('.')[0] in string:
        return "FX"
    elif hs and hs.split('.')[0] in string:
        return "HS"
    elif nk and nk.split('.')[0] in string:
        return "NK"
    elif dd and dd.split('.')[0] in string:
        return "DD"
    elif fc and fc.split('.')[0] in string:
        return "FC"
    else:
        return False


def check_valid_release(title, retail_only, hevc_retail, dbfile):
    if retail_only:
        if not is_retail(title, False, False):
            return False

    if ".German" in title:
        search_title = title.split(".German")[0]
    elif ".GERMAN" in title:
        search_title = title.split(".GERMAN")[0]
    else:
        try:
            quality = re.findall(r"\d{3,4}p", title)[0]
            search_title = title.split(quality)[0]
        except:
            return True

    db = RssDb(dbfile, 'rsscrawler')
    is_episode = re.findall(r'.*\.s\d{1,3}(e\d{1,3}|e\d{1,3}-.*\d{1,3})\..*', title, re.IGNORECASE)
    if is_episode:
        episode_name = re.findall(r'.*\.s\d{1,3}e\d{1,3}(\..*)', search_title, re.IGNORECASE)
        if episode_name:
            search_title = search_title.replace(episode_name[0], "")
        season_search_title = search_title.replace(is_episode[0], "") + "."
        season_results = db.retrieve_all_beginning_with(season_search_title)
        results = db.retrieve_all_beginning_with(search_title) + season_results
    else:
        db = RssDb(dbfile, 'rsscrawler')
        results = db.retrieve_all_beginning_with(search_title)

    if not results:
        return True

    bluray_tags = [".bd-rip.", ".br-rip.", ".bluray-rip.", ".bluray.", ".bd-disk.", ".bd.", ".bd5.", ".bd9.", ".bd25.",
                   ".bd50."]
    web_tags = [".web.", ".web-rip.", ".webrip.", ".vod-rip.", ".webdl.", ".web-dl.", ".ddc."]
    trash_tags = [".cam.", ".cam-rip.", ".ts.", ".telesync.", ".wp.", ".workprint.", ".tc.", ".telecine.", ".vhs-rip.",
                  ".tv-rip.", ".hdtv-rip.", ".hdtv.", ".tvrip.", ".hdtvrip.", ".sat-rip.", ".dvb-rip.", ".ds-rip.",
                  ".scr.", ".screener.", ".dvdscr.", ".dvdscreener.", ".bdscr.", ".r5.", ".dvdrip.", ".dvd."]

    unknown = []
    trash = []
    web = []
    bluray = []
    retail = []

    # Get all previously found Releases and categorize them by their tags
    for r in results:
        if any(s in r.lower() for s in bluray_tags):
            if is_retail(r, False, False):
                retail.append(r)
            else:
                bluray.append(r)
        elif any(s in r.lower() for s in web_tags):
            web.append(r)
        elif any(s in r.lower() for s in trash_tags):
            trash.append(r)
        else:
            unknown.append(r)

    # Categorize the current Release by its tag to check if a release of the same or better category was already found
    # If no release is in the higher category, propers are allowed anytime
    # If no HEVC is available in the current category or higher and the current release is HEVC, it will be allowed
    if any(s in title.lower() for s in bluray_tags):
        if is_retail(r, False, False):
            if len(retail) > 0:
                if hevc_retail:
                    if is_hevc(title):
                        no_hevc = True
                        for r in retail:
                            if is_hevc(r):
                                no_hevc = False
                        if no_hevc:
                            return True
                if ".proper" in title.lower():
                    return True
                return False
        else:
            if len(retail) == 0 and len(bluray) > 0:
                if ".proper" in title.lower():
                    return True
            if len(retail) > 0 or len(bluray) > 0:
                if hevc_retail:
                    if is_hevc(title):
                        no_hevc = True
                        for r in retail + bluray:
                            if is_hevc(r):
                                no_hevc = False
                        if no_hevc:
                            return True
                return False
    elif any(s in title.lower() for s in web_tags):
        if len(retail) == 0 and len(bluray) == 0 and len(web) > 0:
            if ".proper" in title.lower():
                return True
        if len(retail) > 0 or len(bluray) > 0 or len(web) > 0:
            if hevc_retail:
                if is_hevc(title):
                    no_hevc = True
                    for r in retail + bluray + web:
                        if is_hevc(r):
                            no_hevc = False
                    if no_hevc:
                        return True
            return False
    elif any(s in title.lower() for s in trash_tags):
        if len(retail) == 0 and len(bluray) == 0 and len(web) == 0 and len(trash) > 0:
            if ".proper" in title.lower():
                return True
        if len(retail) > 0 or len(bluray) > 0 or len(web) > 0 or len(trash) > 0:
            return False
    else:
        if len(retail) == 0 and len(bluray) == 0 and len(web) == 0 and len(trash) == 0 and len(unknown) > 0:
            if ".proper" in title.lower():
                return True
        if len(retail) > 0 or len(bluray) > 0 or len(web) > 0 or len(trash) > 0 or len(unknown) > 0:
            return False

    return True


def decode_base64(value):
    value = value.replace("-", "/")
    return base64.b64decode(value).decode()


def encode_base64(value):
    return base64.b64encode(value.encode("utf-8")).decode().replace("/", "-")


def fullhd_title(key):
    return key.replace("720p", "DL.1080p")


def get_to_decrypt(dbfile):
    try:
        to_decrypt = RssDb(dbfile, 'to_decrypt').retrieve_all_titles()
        if to_decrypt:
            packages = []
            for package in to_decrypt:
                title = package[0]
                details = package[1].split('|')
                url = details[0]
                password = details[1]
                packages.append({
                    'name': title,
                    'url': url,
                    'password': password
                })
            return packages
        else:
            return False
    except:
        return False


def is_device(device):
    return isinstance(device, (type, myjdapi.Jddevice))


def is_hevc(key):
    key = key.lower()
    if "h265" in key or "x265" in key or "hevc" in key:
        return True
    else:
        return False


def is_retail(key, identifier, dbfile):
    retailfinder = re.search(
        r'(|.UNRATED.*|.Unrated.*|.Uncut.*|.UNCUT.*)(|.Directors.Cut.*|.Final.Cut.*|.DC.*|.REMASTERED.*|.EXTENDED.*|.Extended.*|.Theatrical.*|.THEATRICAL.*)(|.3D.*|.3D.HSBS.*|.3D.HOU.*|.HSBS.*|.HOU.*).(German|GERMAN)(|.AC3|.DTS|.DTS-HD)(|.DL)(|.AC3|.DTS|.DTS-HD)(|.NO.SUBS).(2160|1080|720)p.(UHD.|Ultra.HD.|)(HDDVD|BluRay)(|.HDR)(|.AVC|.AVC.REMUX|.x264|.h264|.x265|.h265|.HEVC)(|.REPACK|.RERiP|.REAL.RERiP)-.*',
        key)
    if retailfinder:
        # If these are False, just a retail check is desired
        if identifier and dbfile:
            remove(key, identifier, dbfile)
        return True
    else:
        return False


def longest_substr(data):
    substr = ''
    if len(data) > 1 and len(data[0]) > 0:
        for i in range(len(data[0])):
            for j in range(len(data[0]) - i + 1):
                if j > len(substr) and all(data[0][i:i + j] in x for x in data):
                    substr = data[0][i:i + j]
    return substr


def readable_size(size):
    if size:
        power = 2 ** 10
        n = 0
        powers = {0: '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
        while size > power:
            size /= power
            n += 1
        size = round(size, 2)
        size = str(size) + " " + powers[n] + 'B'
        return size
    else:
        return ""


def readable_time(time):
    try:
        if not time:
            time = 0
    except:
        time = 0
    time = str(datetime.timedelta(seconds=int(time)))
    if len(time) == 7:
        time = "0" + time
    return time


def remove(retailtitel, identifier, dbfile):
    titles = retail_sub(retailtitel)
    retail = titles[0]
    retailyear = titles[1]
    if identifier == '2':
        liste = "MB_3D"
    else:
        liste = "MB_Filme"
    cont = ListDb(dbfile, liste).retrieve()
    new_cont = []
    if cont:
        for line in cont:
            if line.lower() == retailyear.lower() or line.lower() == retail.lower():
                line = re.sub(r'^(' + re.escape(retailyear.lower()) + '|' + re.escape(retail.lower()) + ')', '',
                              line.lower())
            if line:
                new_cont.append(line)
    ListDb(dbfile, liste).store_list(new_cont)
    log_debug(retail + " durch Cutoff aus " + liste + " entfernt.")


def remove_decrypt(title, dbfile):
    try:
        RssDb(dbfile, 'to_decrypt').delete(title)
        return True
    except:
        return False


def retail_sub(title):
    simplified = title.replace(".", " ")
    retail = re.sub(
        r'(|.UNRATED.*|.Unrated.*|.Uncut.*|.UNCUT.*)(|.Directors.Cut.*|.Final.Cut.*|.DC.*|.REMASTERED.*|.EXTENDED.*|.Extended.*|.Theatrical.*|.THEATRICAL.*)(|.3D.*|.3D.HSBS.*|.3D.HOU.*|.HSBS.*|.HOU.*)(|.)\d{4}(|.)(|.UNRATED.*|.Unrated.*|.Uncut.*|.UNCUT.*)(|.Directors.Cut.*|.Final.Cut.*|.DC.*|.REMASTERED.*|.EXTENDED.*|.Extended.*|.Theatrical.*|.THEATRICAL.*)(|.3D.*|.3D.HSBS.*|.3D.HOU.*|.HSBS.*|.HOU.*).(German|GERMAN)(|.AC3|.DTS|.DTS-HD)(|.DL)(|.AC3|.DTS)(|.NO.SUBS).(2160|1080|720)p.(UHD.|Ultra.HD.|)(HDDVD|BluRay)(|.HDR)(|.AVC|.AVC.REMUX|.x264|.h264|.x265|.h265|.HEVC)(|.REPACK|.RERiP|.REAL.RERiP)-.*',
        "", simplified)
    retailyear = re.sub(
        r'(|.UNRATED.*|.Unrated.*|.Uncut.*|.UNCUT.*)(|.Directors.Cut.*|.Final.Cut.*|.DC.*|.REMASTERED.*|.EXTENDED.*|.Extended.*|.Theatrical.*|.THEATRICAL.*)(|.3D.*|.3D.HSBS.*|.3D.HOU.*|.HSBS.*|.HOU.*).(German|GERMAN)(|.AC3|.DTS|.DTS-HD)(|.DL)(|.AC3|.DTS|.DTS-HD)(|.NO.SUBS).(2160|1080|720)p.(UHD.|Ultra.HD.|)(HDDVD|BluRay)(|.HDR)(|.AVC|.AVC.REMUX|.x264|.h264|.x265|.h265|.HEVC)(|.REPACK|.RERiP|.REAL.RERiP)-.*',
        "", simplified)
    return retail, retailyear


def rreplace(s, old, new, occurrence):
    li = s.rsplit(old, occurrence)
    return new.join(li)


def sanitize(key):
    key = key.replace('.', ' ').replace(';', '').replace(',', '').replace(u'Ä', 'Ae').replace(u'ä', 'ae').replace('ä',
                                                                                                                  'ae').replace(
        u'Ö', 'Oe').replace(u'ö', 'oe').replace(u'Ü', 'Ue').replace(u'ü', 'ue').replace(u'ß', 'ss').replace('(',
                                                                                                            '').replace(
        ')', '').replace('*', '').replace('|', '').replace('\\', '').replace('/', '').replace('?', '').replace('!',
                                                                                                               '').replace(
        ':', '').replace('  ', ' ').replace("'", '').replace("- ", "")
    return key
