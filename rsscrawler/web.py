# -*- coding: utf-8 -*-
# RSScrawler
# Projekt von https://github.com/rix1337

from logging import handlers

import ast
import gevent
import json
import logging
import os
import re
import sys
import time
from flask import Flask, request, redirect, send_from_directory, render_template, jsonify, Response
from functools import wraps
from gevent.pywsgi import WSGIServer
from passlib.hash import pbkdf2_sha256
from requests.packages.urllib3 import disable_warnings as disable_request_warnings
from requests.packages.urllib3.exceptions import InsecureRequestWarning

import rsscrawler.myjdapi
from rsscrawler import search
from rsscrawler import version
from rsscrawler.common import Unbuffered
from rsscrawler.common import decode_base64
from rsscrawler.common import get_to_decrypt
from rsscrawler.common import is_device
from rsscrawler.common import remove_decrypt
from rsscrawler.common import rreplace
from rsscrawler.config import RssConfig
from rsscrawler.db import ListDb
from rsscrawler.db import RssDb
from rsscrawler.myjd import check_device
from rsscrawler.myjd import do_add_decrypted
from rsscrawler.myjd import do_package_replace
from rsscrawler.myjd import download
from rsscrawler.myjd import get_device
from rsscrawler.myjd import get_if_one_device
from rsscrawler.myjd import get_info
from rsscrawler.myjd import get_packages_in_linkgrabber
from rsscrawler.myjd import get_state
from rsscrawler.myjd import jdownloader_pause
from rsscrawler.myjd import jdownloader_start
from rsscrawler.myjd import jdownloader_stop
from rsscrawler.myjd import move_to_downloads
from rsscrawler.myjd import package_merge
from rsscrawler.myjd import remove_from_linkgrabber
from rsscrawler.myjd import retry_decrypt
from rsscrawler.myjd import update_jdownloader
from rsscrawler.notifiers import notify


def app_container(port, local_address, docker, configfile, dbfile, log_file, no_logger, _device):
    global device
    device = _device
    global helper_active
    helper_active = False
    global already_added
    already_added = []

    base_dir = '.'
    if getattr(sys, 'frozen', False):
        base_dir = os.path.join(sys._MEIPASS)

    app = Flask(__name__, template_folder=os.path.join(base_dir, 'web'))
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    general = RssConfig('RSScrawler', configfile)
    if general.get("prefix"):
        prefix = '/' + general.get("prefix")
    else:
        prefix = ""

    def check_auth(config, username, password):
        auth_hash = config.get("auth_hash")
        if auth_hash and "$pbkdf2-sha256" not in auth_hash:
            auth_hash = pbkdf2_sha256.hash(auth_hash)
            config.save(
                "auth_hash", to_str(auth_hash))
        return username == config.get("auth_user") and pbkdf2_sha256.verify(password, auth_hash)

    def authenticate():
        return Response(
            '''<html>
                <head><title>401 Authorization Required</title></head>
                <body bgcolor="white">
                <center><h1>401 Authorization Required</h1></center>
                <hr><center>RSScrawler</center>
                </body>
                </html>
                ''', 401,
            {'WWW-Authenticate': 'Basic realm="RSScrawler"'})

    def requires_auth(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            config = RssConfig('RSScrawler', configfile)
            if config.get("auth_user") and config.get("auth_hash"):
                auth = request.authorization
                if not auth or not check_auth(config, auth.username, auth.password):
                    return authenticate()
            return f(*args, **kwargs)

        return decorated

    def to_int(i):
        if isinstance(i, bytes):
            i = i.decode()
        i = str(i).strip().replace("None", "")
        return int(i) if i else ""

    def to_float(i):
        i = str(i).strip().replace("None", "")
        return float(i) if i else ""

    def to_str(i):
        return '' if i is None else str(i)

    def to_bool(i):
        return True if i == "True" else False

    if prefix:
        @app.route('/')
        @requires_auth
        def index_prefix():
            return redirect(prefix)

    @app.route(prefix + '/<path:path>')
    @requires_auth
    def send_html(path):
        return send_from_directory(os.path.join(base_dir, 'web'), path)

    @app.route(prefix + '/')
    @requires_auth
    def index():
        return render_template('index.html')

    @app.route(prefix + "/api/log/", methods=['GET', 'DELETE'])
    @requires_auth
    def get_delete_log():
        if request.method == 'GET':
            log = []
            if os.path.isfile(log_file):
                logfile = open(log_file)
                i = 0
                for line in reversed(logfile.readlines()):
                    if line and line != "\n":
                        payload = [i]
                        line = line.replace("]", "")
                        line = line.replace("[", "")
                        line = re.sub(r",\d{3}", "", line)
                        line = line.split(" - ")
                        for line_part in line:
                            payload.append(line_part)
                        log.append(payload)
                    i += 1
            return jsonify(
                {
                    "log": log,
                }
            )
        elif request.method == 'DELETE':
            open(log_file, 'w').close()
            return "Success", 200
        else:
            return "Failed", 405

    @app.route(prefix + "/api/log_row/<row>", methods=['DELETE'])
    @requires_auth
    def get_delete_log_row(row):
        row = to_int(row)
        if request.method == 'DELETE':
            log = []
            if os.path.isfile(log_file):
                logfile = open(log_file)
                i = 0
                for line in reversed(logfile.readlines()):
                    if line and line != "\n":
                        if i != row:
                            log.append(line)
                    i += 1
                log = "".join(reversed(log))
                with open(log_file, 'w') as file:
                    file.write(log)
            return "Success", 200
        else:
            return "Failed", 405

    @app.route(prefix + "/api/settings/", methods=['GET', 'POST'])
    @requires_auth
    def get_post_settings():
        if request.method == 'GET':
            general_conf = RssConfig('RSScrawler', configfile)
            hosters = RssConfig('Hosters', configfile)
            alerts = RssConfig('Notifications', configfile)
            ombi = RssConfig('Ombi', configfile)
            crawljobs = RssConfig('Crawljobs', configfile)
            mb_conf = RssConfig('MB', configfile)
            sj_conf = RssConfig('SJ', configfile)
            dj_conf = RssConfig('DJ', configfile)
            dd_conf = RssConfig('DD', configfile)
            if not mb_conf.get("crawl3dtype"):
                crawl_3d_type = "hsbs"
            else:
                crawl_3d_type = mb_conf.get("crawl3dtype")
            return jsonify(
                {
                    "settings": {
                        "general": {
                            "auth_user": general_conf.get("auth_user"),
                            "auth_hash": general_conf.get("auth_hash"),
                            "myjd_user": general_conf.get("myjd_user"),
                            "myjd_pass": general_conf.get("myjd_pass"),
                            "myjd_device": general_conf.get("myjd_device"),
                            "port": to_int(general_conf.get("port")),
                            "prefix": general_conf.get("prefix"),
                            "interval": to_int(general_conf.get("interval")),
                            "english": general_conf.get("english"),
                            "surround": general_conf.get("surround"),
                            "proxy": general_conf.get("proxy"),
                            "fallback": general_conf.get("fallback"),
                            "closed_myjd_tab": general_conf.get("closed_myjd_tab"),
                            "one_mirror_policy": general_conf.get("one_mirror_policy"),
                            "packages_per_myjd_page": to_int(general_conf.get("packages_per_myjd_page")),
                        },
                        "hosters": {
                            "rapidgator": hosters.get("rapidgator"),
                            "turbobit": hosters.get("turbobit"),
                            "uploaded": hosters.get("uploaded"),
                            "zippyshare": hosters.get("zippyshare"),
                            "oboom": hosters.get("oboom"),
                            "ddl": hosters.get("ddl"),
                            "filefactory": hosters.get("filefactory"),
                            "uptobox": hosters.get("uptobox"),
                            "onefichier": hosters.get("1fichier"),
                            "filer": hosters.get("filer"),
                            "nitroflare": hosters.get("nitroflare"),
                            "ironfiles": hosters.get("ironfiles"),
                            "k2s": hosters.get("k2s"),
                        },
                        "alerts": {
                            "pushbullet": alerts.get("pushbullet"),
                            "pushover": alerts.get("pushover"),
                            "homeassistant": alerts.get("homeassistant"),
                            "telegram": alerts.get("telegram"),
                        },
                        "ombi": {
                            "url": ombi.get("url"),
                            "api": ombi.get("api"),
                        },
                        "crawljobs": {
                            "autostart": crawljobs.get("autostart"),
                            "subdir": crawljobs.get("subdir"),
                        },
                        "mb": {
                            "quality": mb_conf.get("quality"),
                            "search": mb_conf.get("search"),
                            "ignore": mb_conf.get("ignore"),
                            "regex": mb_conf.get("regex"),
                            "imdb_score": to_float(mb_conf.get("imdb")),
                            "imdb_year": to_int(mb_conf.get("imdbyear")),
                            "force_dl": mb_conf.get("enforcedl"),
                            "cutoff": mb_conf.get("cutoff"),
                            "crawl_3d": mb_conf.get("crawl3d"),
                            "crawl_3d_type": crawl_3d_type,
                            "hevc_retail": mb_conf.get("hevc_retail"),
                            "retail_only": mb_conf.get("retail_only"),
                            "hoster_fallback": mb_conf.get("hoster_fallback"),
                        },
                        "sj": {
                            "quality": sj_conf.get("quality"),
                            "ignore": sj_conf.get("rejectlist"),
                            "regex": sj_conf.get("regex"),
                            "hevc_retail": sj_conf.get("hevc_retail"),
                            "retail_only": sj_conf.get("retail_only"),
                            "hoster_fallback": sj_conf.get("hoster_fallback"),
                        },
                        "mbsj": {
                            "enabled": mb_conf.get("crawlseasons"),
                            "quality": mb_conf.get("seasonsquality"),
                            "packs": mb_conf.get("seasonpacks"),
                            "source": mb_conf.get("seasonssource"),
                        },
                        "dj": {
                            "quality": dj_conf.get("quality"),
                            "ignore": dj_conf.get("rejectlist"),
                            "regex": dj_conf.get("regex"),
                            "hoster_fallback": dj_conf.get("hoster_fallback"),
                        },
                        "dd": {
                            "feeds": dd_conf.get("feeds"),
                            "hoster_fallback": dd_conf.get("hoster_fallback"),
                        }
                    }
                }
            )
        if request.method == 'POST':
            data = request.json

            section = RssConfig("RSScrawler", configfile)

            section.save(
                "auth_user", to_str(data['general']['auth_user']))

            auth_hash = data['general']['auth_hash']
            if auth_hash and "$pbkdf2-sha256" not in auth_hash:
                auth_hash = pbkdf2_sha256.hash(auth_hash)
            section.save(
                "auth_hash", to_str(auth_hash))

            myjd_user = to_str(data['general']['myjd_user'])
            myjd_pass = to_str(data['general']['myjd_pass'])
            myjd_device = to_str(data['general']['myjd_device'])

            if myjd_user and myjd_pass and not myjd_device:
                myjd_device = get_if_one_device(myjd_user, myjd_pass)
                if myjd_device:
                    print(u"Gerätename " + myjd_device + " automatisch ermittelt.")

            if myjd_user and myjd_pass and myjd_device:
                device_check = check_device(myjd_user, myjd_pass, myjd_device)
                if not device_check:
                    myjd_device = get_if_one_device(myjd_user, myjd_pass)
                    if myjd_device:
                        print(u"Gerätename " + myjd_device + " automatisch ermittelt.")
                    else:
                        print(u"Fehlerhafte My JDownloader Zugangsdaten. Bitte vor dem Speichern prüfen!")
                        return "Failed", 400

            section.save("myjd_user", myjd_user)
            section.save("myjd_pass", myjd_pass)
            section.save("myjd_device", myjd_device)
            section.save("port", to_str(data['general']['port']))
            section.save("prefix", to_str(data['general']['prefix']).lower())
            interval = to_str(data['general']['interval'])
            if to_int(interval) < 5:
                interval = '5'
            section.save("interval", interval)
            section.save("english", to_str(data['general']['english']))
            section.save("surround", to_str(data['general']['surround']))
            section.save("proxy", to_str(data['general']['proxy']))
            section.save("fallback", to_str(data['general']['fallback']))
            section.save("closed_myjd_tab", to_str(data['general']['closed_myjd_tab']))
            section.save("one_mirror_policy", to_str(data['general']['one_mirror_policy']))
            section.save("packages_per_myjd_page", to_str(data['general']['packages_per_myjd_page']))

            section = RssConfig("Crawljobs", configfile)

            section.save("autostart", to_str(data['crawljobs']['autostart']))
            section.save("subdir", to_str(data['crawljobs']['subdir']))

            section = RssConfig("Notifications", configfile)

            section.save("pushbullet", to_str(data['alerts']['pushbullet']))
            section.save("pushover", to_str(data['alerts']['pushover']))
            section.save("telegram", to_str(data['alerts']['telegram']))
            section.save("homeassistant", to_str(data['alerts']['homeassistant']))

            section = RssConfig("Hosters", configfile)

            section.save("rapidgator", to_str(data['hosters']['rapidgator']))
            section.save("turbobit", to_str(data['hosters']['turbobit']))
            section.save("uploaded", to_str(data['hosters']['uploaded']))
            section.save("zippyshare", to_str(data['hosters']['zippyshare']))
            section.save("oboom", to_str(data['hosters']['oboom']))
            section.save("ddl", to_str(data['hosters']['ddl']))
            section.save("filefactory", to_str(data['hosters']['filefactory']))
            section.save("uptobox", to_str(data['hosters']['uptobox']))
            section.save("1fichier", to_str(data['hosters']['onefichier']))
            section.save("filer", to_str(data['hosters']['filer']))
            section.save("nitroflare", to_str(data['hosters']['nitroflare']))
            section.save("ironfiles", to_str(data['hosters']['ironfiles']))
            section.save("k2s", to_str(data['hosters']['k2s']))

            section = RssConfig("Ombi", configfile)

            section.save("url", to_str(data['ombi']['url']))
            section.save("api", to_str(data['ombi']['api']))

            section = RssConfig("MB", configfile)
            section.save("quality", to_str(data['mb']['quality']))
            section.save("search", to_str(data['mb']['search']))
            section.save("ignore", to_str(data['mb']['ignore']).lower())
            section.save("regex", to_str(data['mb']['regex']))
            section.save("cutoff", to_str(data['mb']['cutoff']))
            section.save("crawl3d", to_str(data['mb']['crawl_3d']))
            section.save("crawl3dtype", to_str(data['mb']['crawl_3d_type']))
            section.save("enforcedl", to_str(data['mb']['force_dl']))
            section.save("crawlseasons", to_str(data['mbsj']['enabled']))
            section.save("seasonsquality", to_str(data['mbsj']['quality']))
            section.save("seasonpacks", to_str(data['mbsj']['packs']))
            section.save("seasonssource", to_str(data['mbsj']['source']).lower())
            section.save("imdbyear", to_str(data['mb']['imdb_year']))
            imdb = to_str(data['mb']['imdb_score'])
            if re.match('[^0-9]', imdb):
                imdb = 0.0
            elif imdb == '':
                imdb = 0.0
            else:
                imdb = round(float(to_str(data['mb']['imdb_score']).replace(",", ".")), 1)
            if imdb > 10:
                imdb = 10.0
            section.save("imdb", to_str(imdb))
            section.save("hevc_retail", to_str(data['mb']['hevc_retail']))
            section.save("retail_only", to_str(data['mb']['retail_only']))
            section.save("hoster_fallback", to_str(data['mb']['hoster_fallback']))

            section = RssConfig("SJ", configfile)

            section.save("quality", to_str(data['sj']['quality']))
            section.save("rejectlist", to_str(data['sj']['ignore']).lower())
            section.save("regex", to_str(data['sj']['regex']))
            section.save("hevc_retail", to_str(data['sj']['hevc_retail']))
            section.save("retail_only", to_str(data['sj']['retail_only']))
            section.save("hoster_fallback", to_str(data['sj']['hoster_fallback']))

            section = RssConfig("DJ", configfile)

            section.save("quality", to_str(data['dj']['quality']))
            section.save("rejectlist", to_str(data['dj']['ignore']).lower())
            section.save("regex", to_str(data['dj']['regex']))
            section.save("hoster_fallback", to_str(data['dj']['hoster_fallback']))

            section = RssConfig("DD", configfile)

            section.save("feeds", to_str(data['dd']['feeds']))
            section.save("hoster_fallback", to_str(data['dd']['hoster_fallback']))
            return "Success", 201
        else:
            return "Failed", 405

    @app.route(prefix + "/api/version/", methods=['GET'])
    @requires_auth
    def get_version():
        if request.method == 'GET':
            ver = "v." + version.get_version()
            if version.update_check()[0]:
                updateready = True
                updateversion = version.update_check()[1]
                print(u'Update steht bereit (' + updateversion +
                      ')! Weitere Informationen unter https://github.com/rix1337/RSScrawler/releases/latest')
            else:
                updateready = False
            return jsonify(
                {
                    "version": {
                        "ver": ver,
                        "update_ready": updateready,
                        "docker": docker,
                        "helper_active": helper_active
                    }
                }
            )
        else:
            return "Failed", 405

    @app.route(prefix + "/api/crawltimes/", methods=['GET'])
    @requires_auth
    def get_crawltimes():
        if request.method == 'GET':
            try:
                crawltimes = RssDb(dbfile, "crawltimes")
            except:
                time.sleep(3)
                return "Failed", 400
            return jsonify(
                {
                    "crawltimes": {
                        "active": to_bool(crawltimes.retrieve("active")),
                        "start_time": to_float(crawltimes.retrieve("start_time")),
                        "end_time": to_float(crawltimes.retrieve("end_time")),
                        "total_time": crawltimes.retrieve("total_time"),
                        "next_start": to_float(crawltimes.retrieve("next_start")),
                    }
                }
            )
        else:
            return "Failed", 405

    @app.route(prefix + "/api/hostnames/", methods=['GET'])
    @requires_auth
    def get_hostnames():
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

        if request.method == 'GET':
            try:
                sj = sj.replace("s", "S", 1).replace("j", "J", 1)
                dj = dj.replace("d", "D", 1).replace("j", "J", 1)
                sf = sf.replace("s", "S", 1).replace("f", "F", 1)
                mb = mb.replace("m", "M", 1).replace("b", "B", 1)
                hw = hw.replace("h", "H", 1).replace("d", "D", 1).replace("w", "W", 1)
                hs = hs.replace("h", "H", 1).replace("d", "D", 1).replace("s", "S", 1)
                fx = fx.replace("f", "F", 1).replace("d", "D", 1).replace("x", "X", 1)
                nk = nk.replace("n", "N", 1).replace("k", "K", 1)
                dd = dd.replace("d", "D", 2)
                fc = fc.replace("f", "F", 1).replace("c", "C", 1)
                bl = ' / '.join(list(filter(None, [mb, hw, hs, fx, nk])))
                s = ' / '.join(list(filter(None, [sj, sf])))
                sjbl = ' / '.join(list(filter(None, [s, bl])))

                if not sj:
                    sj = "Nicht gesetzt!"
                if not dj:
                    dj = "Nicht gesetzt!"
                if not sf:
                    sf = "Nicht gesetzt!"
                if not mb:
                    mb = "Nicht gesetzt!"
                if not hw:
                    hw = "Nicht gesetzt!"
                if not hs:
                    hs = "Nicht gesetzt!"
                if not fx:
                    fx = "Nicht gesetzt!"
                if not nk:
                    nk = "Nicht gesetzt!"
                if not dd:
                    dd = "Nicht gesetzt!"
                if not fc:
                    fc = "Nicht gesetzt!"
                if not bl:
                    bl = "Nicht gesetzt!"
                if not s:
                    s = "Nicht gesetzt!"
                if not sjbl:
                    sjbl = "Nicht gesetzt!"
                return jsonify(
                    {
                        "hostnames": {
                            "sj": sj,
                            "dj": dj,
                            "sf": sf,
                            "mb": mb,
                            "hw": hw,
                            "hs": hs,
                            "fx": fx,
                            "nk": nk,
                            "dd": dd,
                            "fc": fc,
                            "bl": bl,
                            "s": s,
                            "sjbl": sjbl
                        }
                    }
                )
            except:
                return "Failed", 400
        else:
            return "Failed", 405

    @app.route(prefix + "/api/blocked_sites/", methods=['GET'])
    @requires_auth
    def get_blocked_sites():
        def check(site, db):
            return to_bool(str(db.retrieve(site)).replace("Blocked", "True"))

        if request.method == 'GET':
            try:
                db_proxy = RssDb(dbfile, 'proxystatus')
                db_normal = RssDb(dbfile, 'normalstatus')
            except:
                time.sleep(3)
                return "Failed", 400
            return jsonify(
                {
                    "proxy": {
                        "SJ": check("SJ", db_proxy),
                        "DJ": check("DJ", db_proxy),
                        "SF": check("SF", db_proxy),
                        "MB": check("MB", db_proxy),
                        "HW": check("HW", db_proxy),
                        "FX": check("FX", db_proxy),
                        "HS": check("HS", db_proxy),
                        "NK": check("NK", db_proxy),
                        "DD": check("DD", db_proxy),
                        "FC": check("FC", db_proxy)
                    },
                    "normal": {
                        "SJ": check("SJ", db_normal),
                        "DJ": check("DJ", db_normal),
                        "SF": check("SF", db_normal),
                        "MB": check("MB", db_normal),
                        "HW": check("HW", db_normal),
                        "FX": check("FX", db_normal),
                        "HS": check("HS", db_normal),
                        "NK": check("NK", db_normal),
                        "DD": check("DD", db_normal),
                        "FC": check("FC", db_normal)
                    }
                }
            )
        else:
            return "Failed", 405

    @app.route(prefix + "/api/search/<title>", methods=['GET'])
    @requires_auth
    def search_title(title):
        if request.method == 'GET':
            results = search.get(title, configfile, dbfile)
            return jsonify(
                {
                    "results": {
                        "bl": results[0],
                        "sj": results[1]
                    }
                }
            ), 200
        else:
            return "Failed", 405

    @app.route(prefix + "/api/download_movie/<title>", methods=['POST'])
    @requires_auth
    def download_movie(title):
        global device
        if request.method == 'POST':
            payload = search.best_result_bl(title, configfile, dbfile)
            if payload:
                matches = search.download_bl(payload, device, configfile, dbfile)
                return "Success: " + str(matches), 200
            else:
                return "Failed", 400
        else:
            return "Failed", 405

    @app.route(prefix + "/api/download_show/<title>", methods=['POST'])
    @requires_auth
    def download_show(title):
        global device
        if request.method == 'POST':
            payload = search.best_result_sj(title, configfile, dbfile)
            if payload:
                matches = search.download_sj(payload, configfile, dbfile)
                if matches:
                    return "Success: " + str(matches), 200
            return "Failed", 400
        else:
            return "Failed", 405

    @app.route(prefix + "/api/download_bl/<payload>", methods=['POST'])
    @requires_auth
    def download_bl(payload):
        global device
        if request.method == 'POST':
            if search.download_bl(payload, device, configfile, dbfile):
                return "Success", 200
            else:
                return "Failed", 400
        else:
            return "Failed", 405

    @app.route(prefix + "/api/download_sj/<payload>", methods=['POST'])
    @requires_auth
    def download_sj(payload):
        global device
        if request.method == 'POST':
            if search.download_sj(payload, configfile, dbfile):
                return "Success", 200
            else:
                return "Failed", 400
        else:
            return "Failed", 405

    @app.route(prefix + "/api/myjd/", methods=['GET'])
    @requires_auth
    def myjd_info():
        global device
        if request.method == 'GET':
            myjd = get_info(configfile, device)
            packages_to_decrypt = get_to_decrypt(dbfile)
            if myjd:
                device = myjd[0]
                return jsonify(
                    {
                        "downloader_state": myjd[1],
                        "grabber_collecting": myjd[2],
                        "update_ready": myjd[3],
                        "packages": {
                            "downloader": myjd[4][0],
                            "linkgrabber_decrypted": myjd[4][1],
                            "linkgrabber_offline": myjd[4][2],
                            "linkgrabber_failed": myjd[4][3],
                            "to_decrypt": packages_to_decrypt
                        }
                    }
                ), 200
            else:
                return "Failed", 400
        else:
            return "Failed", 405

    @app.route(prefix + "/api/myjd_state/", methods=['GET'])
    @requires_auth
    def myjd_state():
        global device
        if request.method == 'GET':
            myjd = get_state(configfile, device)
            if myjd:
                device = myjd[0]
                return jsonify(
                    {
                        "downloader_state": myjd[1],
                        "grabber_collecting": myjd[2]
                    }
                ), 200
            else:
                return "Failed", 400
        else:
            return "Failed", 405

    @app.route(prefix + "/api/myjd_move/<linkids>&<uuids>", methods=['POST'])
    @requires_auth
    def myjd_move(linkids, uuids):
        global device
        if request.method == 'POST':
            linkids_raw = ast.literal_eval(linkids)
            linkids = []
            if isinstance(linkids_raw, (list, tuple)):
                for linkid in linkids_raw:
                    linkids.append(linkid)
            else:
                linkids.append(linkids_raw)
            uuids_raw = ast.literal_eval(uuids)
            uuids = []
            if isinstance(uuids_raw, (list, tuple)):
                for uuid in uuids_raw:
                    uuids.append(uuid)
            else:
                uuids.append(uuids_raw)
            device = move_to_downloads(configfile, device, linkids, uuids)
            if device:
                return "Success", 200
            else:
                return "Failed", 400
        else:
            return "Failed", 405

    @app.route(prefix + "/api/myjd_remove/<linkids>&<uuids>", methods=['POST'])
    @requires_auth
    def myjd_remove(linkids, uuids):
        global device
        if request.method == 'POST':
            linkids_raw = ast.literal_eval(linkids)
            linkids = []
            if isinstance(linkids_raw, (list, tuple)):
                for linkid in linkids_raw:
                    linkids.append(linkid)
            else:
                linkids.append(linkids_raw)
            uuids_raw = ast.literal_eval(uuids)
            uuids = []
            if isinstance(uuids_raw, (list, tuple)):
                for uuid in uuids_raw:
                    uuids.append(uuid)
            else:
                uuids.append(uuids_raw)
            device = remove_from_linkgrabber(configfile, device, linkids, uuids)
            if device:
                return "Success", 200
            else:
                return "Failed", 400
        else:
            return "Failed", 405

    @app.route(prefix + "/api/internal_remove/<name>", methods=['POST'])
    @requires_auth
    def internal_remove(name):
        global device
        if request.method == 'POST':
            delete = remove_decrypt(name, dbfile)
            if delete:
                return "Success", 200
            else:
                return "Failed", 400
        else:
            return "Failed", 405

    @app.route(prefix + "/api/myjd_retry/<linkids>&<uuids>&<b64_links>", methods=['POST'])
    @requires_auth
    def myjd_retry(linkids, uuids, b64_links):
        global device
        if request.method == 'POST':
            linkids_raw = ast.literal_eval(linkids)
            linkids = []
            if isinstance(linkids_raw, (list, tuple)):
                for linkid in linkids_raw:
                    linkids.append(linkid)
            else:
                linkids.append(linkids_raw)
            uuids_raw = ast.literal_eval(uuids)
            uuids = []
            if isinstance(uuids_raw, (list, tuple)):
                for uuid in uuids_raw:
                    uuids.append(uuid)
            else:
                uuids.append(uuids_raw)
            links = decode_base64(b64_links)
            links = links.split("\n")
            device = retry_decrypt(configfile, dbfile, device, linkids, uuids, links)
            if device:
                return "Success", 200
            else:
                return "Failed", 400
        else:
            return "Failed", 405

    @app.route(prefix + "/api/myjd_update/", methods=['POST'])
    @requires_auth
    def myjd_update():
        global device
        if request.method == 'POST':
            device = update_jdownloader(configfile, device)
            if device:
                return "Success", 200
            else:
                return "Failed", 400
        else:
            return "Failed", 405

    @app.route(prefix + "/api/myjd_start/", methods=['POST'])
    @requires_auth
    def myjd_start():
        global device
        if request.method == 'POST':
            device = jdownloader_start(configfile, device)
            if device:
                return "Success", 200
            else:
                return "Failed", 400
        else:
            return "Failed", 405

    @app.route(prefix + "/api/myjd_pause/<bl>", methods=['POST'])
    @requires_auth
    def myjd_pause(bl):
        global device
        bl = json.loads(bl)
        if request.method == 'POST':
            device = jdownloader_pause(configfile, device, bl)
            if device:
                return "Success", 200
            else:
                return "Failed", 400
        else:
            return "Failed", 405

    @app.route(prefix + "/api/myjd_stop/", methods=['POST'])
    @requires_auth
    def myjd_stop():
        global device
        if request.method == 'POST':
            device = jdownloader_stop(configfile, device)
            if device:
                return "Success", 200
            else:
                return "Failed", 400
        else:
            return "Failed", 405

    @app.route(prefix + "/api/myjd_cnl/<uuid>", methods=['POST'])
    @requires_auth
    def myjd_cnl(uuid):
        global device
        if request.method == 'POST':
            failed = get_info(configfile, device)
            if failed:
                device = failed[0]
                decrypted_packages = failed[4][1]
                offline_packages = failed[4][2]
                failed_packages = failed[4][3]
            else:
                failed_packages = False
                decrypted_packages = False
            if not failed_packages:
                return "Failed", 500

            title = False
            old_package = False
            if failed_packages:
                for op in failed_packages:
                    if str(op['uuid']) == str(uuid):
                        title = op['name']
                        old_package = op
                        break

            if not old_package or not title:
                return "Failed", 500

            known_packages = []
            if decrypted_packages:
                for dp in decrypted_packages:
                    known_packages.append(dp['uuid'])
            if offline_packages:
                for op in offline_packages:
                    known_packages.append(op['uuid'])

            cnl_package = False
            grabber_was_collecting = False
            i = 12
            while i > 0:
                i -= 1
                time.sleep(5)
                failed = get_info(configfile, device)
                if failed:
                    device = failed[0]
                    grabber_collecting = failed[2]
                    if grabber_was_collecting or grabber_collecting:
                        grabber_was_collecting = grabber_collecting
                        i -= 1
                        time.sleep(5)
                    else:
                        if not grabber_collecting:
                            decrypted_packages = failed[4][1]
                            offline_packages = failed[4][2]
                            another_device = package_merge(configfile, device, decrypted_packages, title,
                                                           known_packages)[0]
                            if another_device:
                                device = another_device
                                info = get_info(configfile, device)
                                if info:
                                    device = info[0]
                                    grabber_collecting = info[2]
                                    decrypted_packages = info[4][1]
                                    offline_packages = info[4][2]

                            if not grabber_collecting and decrypted_packages:
                                for dp in decrypted_packages:
                                    if dp['uuid'] not in known_packages:
                                        cnl_package = dp
                                        i = 0
                            if not grabber_collecting and offline_packages:
                                for op in offline_packages:
                                    if op['uuid'] not in known_packages:
                                        cnl_package = op
                                        i = 0

            if not cnl_package:
                return "No Package added through Click'n'Load in time!", 504

            replaced = do_package_replace(configfile, dbfile, device, old_package, cnl_package)
            device = replaced[0]
            if device:
                return "Success", 200
            else:
                return "Failed", 400
        else:
            return "Failed", 405

    @app.route(prefix + "/api/internal_cnl/<name>&<password>", methods=['POST'])
    @requires_auth
    def internal_cnl(name, password):
        global device
        if request.method == 'POST':
            failed = get_info(configfile, device)
            if failed:
                device = failed[0]
                decrypted_packages = failed[4][1]
                offline_packages = failed[4][2]
            else:
                decrypted_packages = False

            known_packages = []
            if decrypted_packages:
                for dp in decrypted_packages:
                    known_packages.append(dp['uuid'])
            if offline_packages:
                for op in offline_packages:
                    known_packages.append(op['uuid'])

            cnl_packages = []
            grabber_was_collecting = False
            i = 12
            while i > 0:
                i -= 1
                time.sleep(5)
                failed = get_info(configfile, device)
                if failed:
                    device = failed[0]
                    grabber_collecting = failed[2]
                    if grabber_was_collecting or grabber_collecting:
                        grabber_was_collecting = grabber_collecting
                        i -= 1
                        time.sleep(5)
                    else:
                        if not grabber_collecting:
                            decrypted_packages = failed[4][1]
                            offline_packages = failed[4][2]
                            if not grabber_collecting and decrypted_packages:
                                for dp in decrypted_packages:
                                    if dp['uuid'] not in known_packages:
                                        cnl_packages.append(dp)
                                        i = 0
                            if not grabber_collecting and offline_packages:
                                for op in offline_packages:
                                    if op['uuid'] not in known_packages:
                                        cnl_packages.append(op)
                                        i = 0

            if not cnl_packages:
                return "No Package added through Click'n'Load in time!", 504

            replaced = do_add_decrypted(configfile, dbfile, device, name, password, cnl_packages)
            device = replaced[0]
            if device:
                remove_decrypt(name, dbfile)
                return "Success", 200
            else:
                return "Failed", 400
        else:
            return "Failed", 405

    def get_list(liste):
        cont = ListDb(dbfile, liste).retrieve()
        return "\n".join(cont) if cont else ""

    @app.route(prefix + "/api/lists/", methods=['GET', 'POST'])
    @requires_auth
    def get_post_lists():
        if request.method == 'GET':
            return jsonify(
                {
                    "lists": {
                        "mb": {
                            "filme": get_list('MB_Filme'),
                            "filme3d": get_list('MB_3D'),
                            "regex": get_list('MB_Regex'),
                        },
                        "sj": {
                            "serien": get_list('SJ_Serien'),
                            "regex": get_list('SJ_Serien_Regex'),
                            "staffeln_regex": get_list('SJ_Staffeln_Regex'),
                        },
                        "dj": {
                            "dokus": get_list('DJ_Dokus'),
                            "regex": get_list('DJ_Dokus_Regex'),
                        },
                        "mbsj": {
                            "staffeln": get_list('MB_Staffeln'),
                        }
                    },
                }
            )
        if request.method == 'POST':
            data = request.json
            ListDb(dbfile, "MB_Filme").store_list(
                data['mb']['filme'].split('\n'))
            ListDb(dbfile, "MB_3D").store_list(
                data['mb']['filme3d'].split('\n'))
            ListDb(dbfile, "MB_Staffeln").store_list(
                data['mbsj']['staffeln'].split('\n'))
            ListDb(dbfile, "MB_Regex").store_list(
                data['mb']['regex'].split('\n'))
            ListDb(dbfile, "SJ_Serien").store_list(
                data['sj']['serien'].split('\n'))
            ListDb(dbfile, "SJ_Serien_Regex").store_list(
                data['sj']['regex'].split('\n'))
            ListDb(dbfile, "SJ_Staffeln_Regex").store_list(
                data['sj']['staffeln_regex'].split('\n'))
            ListDb(dbfile, "DJ_Dokus").store_list(
                data['dj']['dokus'].split('\n'))
            ListDb(dbfile, "DJ_Dokus_Regex").store_list(
                data['dj']['regex'].split('\n'))
            return "Success", 201
        else:
            return "Failed", 405

    @app.route(prefix + "/redirect_user/<target>", methods=['GET'])
    @requires_auth
    def redirect_user(target):
        if request.method == 'GET':
            if target == "captcha":
                return redirect("http://getcaptchasolution.com/zuoo67f5cq", code=302)
            elif target == "multihoster":
                return redirect("http://linksnappy.com/?ref=397097", code=302)
            else:
                return "Failed", 405
        else:
            return "Failed", 405

    @app.route(prefix + "/sponsors_helper/rsscrawler_helper_sj.user.js", methods=['GET'])
    @requires_auth
    def rsscrawler_helper_sj():
        hostnames = RssConfig('Hostnames', configfile)
        sj = hostnames.get('sj')
        dj = hostnames.get('dj')
        if request.method == 'GET':
            return """// ==UserScript==
// @name            RSScrawler Helper (SJ)
// @author          rix1337
// @description     Clicks the correct download button on SJ sub pages to speed up Click'n'Load
// @version         0.3.0
// @require         https://ajax.googleapis.com/ajax/libs/jquery/3.5.1/jquery.min.js
// @match           https://""" + sj + """/*
// @match           https://""" + dj + """/*
// @exclude         https://""" + sj + """/serie/search?q=*
// @exclude         https://""" + dj + """/serie/search?q=*
// ==/UserScript==

document.body.addEventListener('mousedown', function (e) {
    if (e.target.tagName != "A") return;
    var anchor = e.target;
    if (anchor.href.search(/""" + sj + """\/serie\//i) != -1) {
        anchor.href = anchor.href + '#' + anchor.text;
    } else if (anchor.href.search(/""" + dj + """\/serie\//i) != -1) {
        anchor.href = anchor.href + '#' + anchor.text;
    }
});

var tag = window.location.hash.replace("#", "").split('|');
var title = tag[0];
var password = tag[1];
if (title) {
    $('.wrapper').prepend('<h3>[RSScrawler Helper] ' + title + '</h3>');
    $(".container").hide();
    var checkExist = setInterval(async function () {
        if ($("tr:contains('" + title + "')").length) {
            $(".container").show();
            $("tr:contains('" + title + "')")[0].lastChild.firstChild.click();
            clearInterval(checkExist);
        }
    }, 100);
}

""", 200
        else:
            return "Failed", 405

    @app.route(prefix + "/sponsors_helper/rsscrawler_sponsors_helper_sj.user.js", methods=['GET'])
    @requires_auth
    def rsscrawler_sponsors_helper_sj():
        if not helper_active:
            return "Forbidden", 403
        hostnames = RssConfig('Hostnames', configfile)
        sj = hostnames.get('sj')
        dj = hostnames.get('dj')
        if request.method == 'GET':
            return """// ==UserScript==
// @name            RSScrawler Sponsors Helper (SJ)
// @author          rix1337
// @description     Clicks the correct download button on SJ sub pages to speed up Click'n'Load
// @version         0.3.0
// @require         https://ajax.googleapis.com/ajax/libs/jquery/3.5.1/jquery.min.js
// @match           https://""" + sj + """/*
// @match           https://""" + dj + """/*
// @exclude         https://""" + sj + """/serie/search?q=*
// @exclude         https://""" + dj + """/serie/search?q=*
// ==/UserScript==
var sponsorsURL = '""" + local_address + """';
// Hier kann ein Wunschhoster eingetragen werden (ohne www. und .tld):
var sponsorsHoster = '';

document.body.addEventListener('mousedown', function (e) {
    if (e.target.tagName != "A") return;
    var anchor = e.target;
    if (anchor.href.search(/""" + sj + """\/serie\//i) != -1) {
        anchor.href = anchor.href + '#' + anchor.text;
    } else if (anchor.href.search(/""" + dj + """\/serie\//i) != -1) {
        anchor.href = anchor.href + '#' + anchor.text;
    }
});

function Sleep(milliseconds) {
   return new Promise(resolve => setTimeout(resolve, milliseconds));
}


var tag = window.location.hash.replace("#", "").split('|');
var title = tag[0];
var password = tag[1];
if (title) {
    $('.wrapper').prepend('<h3>[RSScrawler Sponsors Helper] ' + title + '</h3>');
    $(".container").hide();
    var checkExist = setInterval(async function() {
        if ($("tr:contains('" + title + "')").length) {
            $(".container").show();
            $("tr:contains('" + title + "')")[0].lastChild.firstChild.click();
            if (sponsorsHelper) {
                console.log("[RSScrawler Sponsors Helper] clicked Download button of " + title);
                await Sleep(500);
                var requiresLogin = $(".alert-warning").length;
                if (requiresLogin) {
                    clearInterval(checkExist);
                }
                $("button:contains('filer')").click();
                $("button:contains('turbo')").click();
                if (sponsorsHoster) {
                    $("button:contains('" + sponsorsHoster + "')").click();
                }
                console.log("[RSScrawler Sponsors Helper] clicked Download button to trigger reCAPTCHA");
            }
            clearInterval(checkExist);
        }
    }, 100);

    var dlExists = setInterval(function() {
        if ($("tr:contains('Download Part')").length) {
            var items = $("tr:contains('Download Part')").find("a");
            var links = [];
            items.each(function(index){
                links.push(items[index].href);
            })
            console.log("[RSScrawler Sponsors Helper] found download links: " + links);
            clearInterval(dlExists);
            window.open(sponsorsURL + '/sponsors_helper/to_download/' + btoa(links + '|' + title + '|' + password));
            // window.close() requires dom.allow_scripts_to_close_windows in Firefox
            window.close();
        }
    }, 100);
}
""", 200
        else:
            return "Failed", 405

    @app.route(prefix + "/sponsors_helper/rsscrawler_sponsors_helper_fc.user.js", methods=['GET'])
    @requires_auth
    def rsscrawler_sponsors_helper_fc():
        if not helper_active:
            return "Forbidden", 403
        hostnames = RssConfig('Hostnames', configfile)
        fc = hostnames.get('fc').replace("www.", "")
        if request.method == 'GET':
            return """// ==UserScript==
// @name            RSScrawler Sponsors Helper (FC)
// @author          rix1337
// @description     Forwards Click'n'Load to RSScrawler
// @version         0.3.4
// @match           https://*.""" + fc + """/*
// @match           https://*.""" + fc.replace("cc", "co") + """/*
// ==/UserScript==
// Hier muss die von außen erreichbare Adresse des RSScrawlers stehen (nicht bspw. die Docker-interne):
var sponsorsURL = '""" + local_address + """';
// Hier kann ein Wunschhoster eingetragen werden (ohne www. und .tld):
var sponsorsHoster = '';

var tag = window.location.hash.replace("#", "").split('|');
var title = tag[0]
var password = tag[1]
var ids = tag[2]
var urlParams = new URLSearchParams(window.location.search);


function Sleep(milliseconds) {
    return new Promise(resolve => setTimeout(resolve, milliseconds));
}

    var mirrorsAvailable = false;
    try {
        mirrorsAvailable = document.querySelector('.mirror').querySelectorAll("a");
    } catch {}
    var cnlAllowed = false;

if (mirrorsAvailable && sponsorsHoster) {
    const currentURL = window.location.href;
    var desiredMirror = "";
    var i;
    for (i = 0; i < mirrorsAvailable.length; i++) {
        if (mirrorsAvailable[i].text.includes(sponsorsHoster)) {
            var ep = "";
            var cur_ep = urlParams.get('episode');
            if (cur_ep) {
                ep = "&episode=" + cur_ep;
            }
            desiredMirror = mirrorsAvailable[i].href + ep + window.location.hash;
        }
    }

    if (desiredMirror) {
        if (!currentURL.includes(desiredMirror)) {
            console.log("[RSScrawler Sponsors Helper] switching to desired Mirror: " + sponsorsHoster);
            window.location = desiredMirror;
        } else {
            console.log("[RSScrawler Sponsors Helper] already at the desired Mirror: " + sponsorsHoster);
            cnlAllowed = true;
        }
    } else {
        console.log("[RSScrawler Sponsors Helper] desired Mirror not available: " + sponsorsHoster);
        cnlAllowed = true;
    }
} else {
    cnlAllowed = true;
}


var cnlExists = setInterval(async function() {
    if (cnlAllowed && document.getElementsByClassName("cnlform").length) {
        clearInterval(cnlExists);
        document.getElementById("cnl_btn").click();
        console.log("[RSScrawler Sponsors Helper] attempting Click'n'Load");
        await Sleep(4000);
        window.close();
    }
}, 100);
""", 200
        else:
            return "Failed", 405

    @app.route(prefix + "/sponsors_helper/", methods=['GET'])
    @requires_auth
    def to_decrypt():
        global helper_active
        helper_active = True
        if request.method == 'GET':
            return render_template('helper.html')
        else:
            return "Failed", 405

    @app.route(prefix + "/sponsors_helper/api/to_decrypt/", methods=['GET'])
    @requires_auth
    def to_decrypt_api():
        if request.method == 'GET':
            global device

            decrypt_name = False
            decrypt_url = False
            decrypt = get_to_decrypt(dbfile)
            if decrypt:
                decrypt = decrypt[0]
                decrypt_name = decrypt["name"]
                decrypt_url = decrypt["url"] + "#" + decrypt_name + "|" + decrypt["password"]

            return jsonify(
                {
                    "to_decrypt": {
                        "name": decrypt_name,
                        "url": decrypt_url,
                    }
                }
            )
        else:
            return "Failed", 405

    @app.route(prefix + "/sponsors_helper/to_download/<payload>", methods=['GET'])
    @requires_auth
    def to_download(payload):
        global device
        global already_added
        hostnames = RssConfig('Hostnames', configfile)
        sj = hostnames.get('sj')
        dj = hostnames.get('dj')
        sf = hostnames.get('sf')
        mb = hostnames.get('mb')
        hw = hostnames.get('hw')
        hs = hostnames.get('hs')
        fx = hostnames.get('fx')
        nk = hostnames.get('nk')
        fc = hostnames.get('fc')

        check_replace = [sj, dj, sf, mb, hw, hs, fx, nk, fc]
        to_replace = []
        for check_name in check_replace:
            if check_name:
                to_replace.append(" - " + check_name)
                if " - " + check_name.replace("www.", "") not in to_replace:
                    to_replace.append(" - " + check_name.replace("www.", ""))

        if request.method == 'GET':
            try:
                payload = decode_base64(payload).split("|")
            except:
                return "Failed", 400
            if payload:
                links = payload[0]
                name = payload[1]
                try:
                    for r in to_replace:
                        name = name.replace(r, "")
                except:
                    pass
                try:
                    password = payload[2]
                except:
                    password = ""
                try:
                    ids = payload[3]
                except:
                    ids = False
                try:
                    dlc = re.findall(r"DownloadDLC\(\'(.*)\'\)", links)
                    if dlc:
                        links = "https://" + fc + "/DLC/" + dlc[0] + ".dlc"
                except:
                    pass

                RssDb(dbfile, 'crawldog').store(name, 'added')
                if device:
                    if ids:
                        try:
                            ids = ids.replace("%20", "").split(";")
                            linkids = ids[0]
                            uuids = ids[1]
                        except:
                            linkids = False
                            uuids = False
                        if ids and uuids:
                            linkids_raw = ast.literal_eval(linkids)
                            linkids = []
                            if isinstance(linkids_raw, (list, tuple)):
                                for linkid in linkids_raw:
                                    linkids.append(linkid)
                            else:
                                linkids.append(linkids_raw)
                            uuids_raw = ast.literal_eval(uuids)
                            uuids = []
                            if isinstance(uuids_raw, (list, tuple)):
                                for uuid in uuids_raw:
                                    uuids.append(uuid)
                            else:
                                uuids.append(uuids_raw)

                            remove_from_linkgrabber(configfile, device, linkids, uuids)
                            remove_decrypt(name, dbfile)
                    else:
                        is_episode = re.findall(r'.*\.(S\d{1,3}E\d{1,3})\..*', name)
                        if not is_episode:
                            re_name = rreplace(name.lower(), "-", ".*", 1)
                            re_name = re_name.replace(".untouched", ".*").replace("dd+51", "dd.51")
                            season_string = re.findall(r'.*(s\d{1,3}).*', re_name)
                            if season_string:
                                re_name = re_name.replace(season_string[0], season_string[0] + '.*')
                            codec_tags = [".h264", ".x264"]
                            for tag in codec_tags:
                                re_name = re_name.replace(tag, ".*264")
                            web_tags = [".web-rip", ".webrip", ".webdl", ".web-dl"]
                            for tag in web_tags:
                                re_name = re_name.replace(tag, ".web.*")
                            multigroup = re.findall(r'.*-((.*)\/(.*))', name.lower())
                            if multigroup:
                                re_name = re_name.replace(multigroup[0][0],
                                                          '(' + multigroup[0][1] + '|' + multigroup[0][2] + ')')
                        else:
                            re_name = name
                            season_string = re.findall(r'.*(s\d{1,3}).*', re_name.lower())

                        if season_string:
                            season_string = season_string[0].replace("s", "S")
                        else:
                            season_string = "^unmatchable$"
                        try:
                            packages = get_packages_in_linkgrabber(configfile, device)
                        except rsscrawler.myjdapi.TokenExpiredException:
                            device = get_device(configfile)
                            if not device or not is_device(device):
                                return "Failed", 500
                            packages = get_packages_in_linkgrabber(configfile, device)
                        if packages:
                            failed = packages[0]
                            offline = packages[1]
                            try:
                                if failed:
                                    for package in failed:
                                        if re.match(re.compile(re_name), package['name'].lower()):
                                            episode = re.findall(r'.*\.S\d{1,3}E(\d{1,3})\..*', package['name'])
                                            if episode:
                                                RssDb(dbfile, 'episode_remover').store(name, str(int(episode[0])))
                                            linkids = package['linkids']
                                            uuids = [package['uuid']]
                                            remove_from_linkgrabber(configfile, device, linkids, uuids)
                                            remove_decrypt(name, dbfile)
                                            break
                                if offline:
                                    for package in offline:
                                        if re.match(re.compile(re_name), package['name'].lower()):
                                            episode = re.findall(r'.*\.S\d{1,3}E(\d{1,3})\..*', package['name'])
                                            if episode:
                                                RssDb(dbfile, 'episode_remover').store(name, str(int(episode[0])))
                                            linkids = package['linkids']
                                            uuids = [package['uuid']]
                                            remove_from_linkgrabber(configfile, device, linkids, uuids)
                                            remove_decrypt(name, dbfile)
                                            break
                            except:
                                pass
                        packages = get_to_decrypt(dbfile)
                        if packages:
                            for package in packages:
                                if re.match(re.compile(re_name),
                                            package['name'].lower().replace(".untouched", ".*").replace("dd+51",
                                                                                                        "dd.51")):
                                    episode = re.findall(r'.*\.S\d{1,3}E(\d{1,3})\..*', package['name'])
                                    remove_decrypt(package['name'], dbfile)
                                    if episode:
                                        RssDb(dbfile, 'episode_remover').store(name, str(int(episode[0])))
                                        episode = str(episode[0])
                                        if len(episode) == 1:
                                            episode = "0" + episode
                                        name = name.replace(season_string + ".",
                                                            season_string + "E" + episode + ".")
                                        break
                        time.sleep(1)
                        remove_decrypt(name, dbfile)
                    try:
                        epoch = int(time.time())
                        for item in already_added:
                            if item[0] == name:
                                if int(item[1]) + 30 > epoch:
                                    print(name + u" wurde in den letzten 30 Sekunden bereits hinzugefügt")
                                    return name + u" wurde in den letzten 30 Sekunden bereits hinzugefügt", 400
                                else:
                                    already_added.remove(item)

                        device = download(configfile, dbfile, device, name, "RSScrawler", links, password)
                        try:
                            notify(["[RSScrawler Sponsors Helper erfolgreich] - " + name], configfile)
                        except:
                            print(u"Benachrichtigung konnte nicht versendet werden!")
                        print(u"[RSScrawler Sponsors Helper erfolgreich] - " + name)
                        already_added.append([name, str(epoch)])
                        return "<script type='text/javascript'>" \
                               "function closeWindow(){window.close()}window.onload=closeWindow;</script>" \
                               "This requires dom.allow_scripts_to_close_windows in Firefox to close automatically", 200
                    except:
                        print(name + u" konnte nicht hinzugefügt werden!")
            return "Failed", 400
        else:
            return "Failed", 405

    http_server = WSGIServer(('0.0.0.0', port), app, log=no_logger)
    http_server.serve_forever()


def start(port, local_address, docker, configfile, dbfile, log_level, log_file, log_format, _device):
    sys.stdout = Unbuffered(sys.stdout)

    logger = logging.getLogger('rsscrawler')
    logger.setLevel(log_level)

    console = logging.StreamHandler(stream=sys.stdout)
    formatter = logging.Formatter(log_format)
    console.setLevel(log_level)

    logfile = logging.handlers.RotatingFileHandler(log_file)
    logfile.setFormatter(formatter)
    logfile.setLevel(logging.INFO)

    logger.addHandler(logfile)
    logger.addHandler(console)

    if log_level == 10:
        logfile_debug = logging.handlers.RotatingFileHandler(
            log_file.replace("RSScrawler.log", "RSScrawler_DEBUG.log"))
        logfile_debug.setFormatter(formatter)
        logfile_debug.setLevel(10)
        logger.addHandler(logfile_debug)

    disable_request_warnings(InsecureRequestWarning)

    no_logger = logging.getLogger("gevent").setLevel(logging.WARNING)
    gevent.hub.Hub.NOT_ERROR = (Exception,)

    if version.update_check()[0]:
        updateversion = version.update_check()[1]
        print(u'Update steht bereit (' + updateversion +
              ')! Weitere Informationen unter https://github.com/rix1337/RSScrawler/releases/latest')

    app_container(port, local_address, docker, configfile, dbfile, log_file, no_logger, _device)
