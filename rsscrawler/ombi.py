# -*- coding: utf-8 -*-
# RSScrawler
# Projekt von https://github.com/rix1337

import json
import re

import requests
from bs4 import BeautifulSoup

from rsscrawler import search
from rsscrawler.common import decode_base64
from rsscrawler.common import encode_base64
from rsscrawler.common import sanitize
from rsscrawler.config import RssConfig
from rsscrawler.db import RssDb
from rsscrawler.url import get_url_headers


def get_imdb(url, configfile, dbfile, scraper):
    result = get_url_headers(url, configfile, dbfile,
                             scraper=scraper,
                             headers={'Accept-Language': 'de'}
                             )
    output = result[0].text
    scraper = result[1]
    return output, scraper


def get_title(input):
    try:
        raw_title = re.findall(
            r"<title>(.*) \((?:.*(?:19|20)\d{2})\) - IMDb</title>", input)[0]
    except:
        raw_title = re.findall(
            r'<meta name="title" content="(.*) \((?:.*(?:19|20)\d{2})\) - IMDb"', input)[0]
    return sanitize(raw_title)


def imdb_movie(imdb_id, configfile, dbfile, scraper):
    try:
        result = get_imdb('https://www.imdb.com/title/' +
                          imdb_id, configfile, dbfile, scraper)
        output = result[0]
        scraper = result[1]

        title = get_title(output)

        return title, scraper
    except:
        print(u"[Ombi] - Fehler beim Abruf der IMDb für: " + imdb_id)
        return False, False


def imdb_show(imdb_id, configfile, dbfile, scraper):
    try:
        result = get_imdb('https://www.imdb.com/title/' +
                          imdb_id, configfile, dbfile, scraper)
        output = result[0]
        scraper = result[1]

        title = get_title(output)

        eps = {}
        soup = BeautifulSoup(output, 'lxml')
        seasons = soup.find_all("a", href=re.compile(
            r'.*/title/' + imdb_id + r'/episodes\?season=.*'))
        for season in seasons:
            result = get_imdb("https://www.imdb.com" +
                              season['href'], configfile, dbfile, scraper)
            output = result[0]
            scraper = result[1]

            sn = int(season.text)
            ep = []
            soup = BeautifulSoup(output, 'lxml')
            episodes = soup.find_all("meta", itemprop="episodeNumber")
            for e in episodes:
                ep.append(int(e['content']))
            eps[sn] = ep

        return title, eps, scraper
    except:
        print(u"[Ombi] - Fehler beim Abruf der IMDb für: " + imdb_id)
        return False, False, False


def generate_reg_title(title, counter, quality):
    title = title.replace(':', '').replace(' -', '').replace('!', '').replace(
        ' ', '.').replace("'", '').replace('(', '').replace(')', '')
    title += '\..*.'
    title += counter
    title += '\..*.'
    title += quality
    title += '.*'
    return title


def generate_api_title(title, counter):
    title = title.replace(':', '').replace(' -', '').replace('!', '').replace(
        ' ', '.').replace("'", '').replace('(', '').replace(')', '')
    title += ','
    title += counter
    return title


def ombi(configfile, dbfile, device, log_debug):
    db = RssDb(dbfile, 'Ombi')

    # Liste der aktive Filmsuchen
    list = RssDb(dbfile, 'MB_Filme')
    # Regex Serien für eine bessere suche
    sjregexdb = RssDb(dbfile, 'SJ_Serien_Regex')
    mbregexdb = RssDb(dbfile, 'MB_Regex')
    # Settings for Regex search
    sjfilter = RssConfig('SJ', configfile)
    sjquality = sjfilter.get('quality')
    sjquality = sjquality[:-1]
    sjregex = sjfilter.get('regex')

    mbfilter = RssConfig('MB', configfile)
    mbquality = mbfilter.get('seasonsquality')
    mbquality = mbquality[:-1]
    mbregex = mbfilter.get('regex')
    mbseasons = mbfilter.get('seasonpacks')

    config = RssConfig('Ombi', configfile)
    url = config.get('url')
    api = config.get('api')

    if not url or not api:
        return device

    english = RssConfig('RSScrawler', configfile).get('english')

    try:
        requested_movies = requests.get(
            url + '/api/v1/Request/movie', headers={'ApiKey': api})
        requested_movies = json.loads(requested_movies.text)
        requested_shows = requests.get(
            url + '/api/v1/Request/tv', headers={'ApiKey': api})
        requested_shows = json.loads(requested_shows.text)
    except:
        log_debug("Ombi ist nicht erreichbar!")
        return False

    scraper = False

    for r in requested_movies:
        if bool(r.get("approved")):
            imdb_id = r.get("imdbId")
            # Title aus ombi entnehmen und sonderzeichen entfernen
            movie_tit = r.get("title")
            movie_tit = movie_tit.replace(':', '').replace(
                ' -', '').replace(' ', '.')

            if not bool(r.get("available")):
                # Neue Struktur der DB
                if db.retrieve('movie_' + str(imdb_id)) == 'added':
                    db.delete('movie_' + str(imdb_id))
                    db.store('movie_' + str(imdb_id), 'search')

                elif not db.retrieve('movie_' + str(imdb_id)) == 'search':
                    response = imdb_movie(imdb_id, configfile, dbfile, scraper)
                    title = response[0]
                    if title:
                        scraper = response[1]
                        best_result = search.best_result_bl(
                            title, configfile, dbfile)
                        print(u"Film: " + title + u" durch Ombi hinzugefügt.")
                        if best_result:
                            search.download_bl(
                                best_result, device, configfile, dbfile)
                        if english:
                            title = r.get('title')
                            best_result = search.best_result_bl(
                                title, configfile, dbfile)
                            print(u"Film: " + title +
                                  u"durch Ombi hinzugefügt.")
                            if best_result:
                                search.download_bl(
                                    best_result, device, configfile, dbfile)
                        db.store('movie_' + str(imdb_id), 'search')
                    else:
                        log_debug(
                            "Titel für IMDB-ID nicht abrufbar: " + imdb_id)

            elif bool(r.get("available")):
                # Migration der vorhandenen von added nach available zum angleichen an die neue DB-values
                if db.retrieve('movie_' + str(imdb_id)) == 'added':
                    db.delete('movie_' + str(imdb_id))
                    db.store('movie_' + str(imdb_id), 'available')

                if db.retrieve('movie_' + str(imdb_id)) == 'search':
                    db.delete('movie_' + str(imdb_id))
                    db.store('movie_' + str(imdb_id), 'available')

                if not db.retrieve('movie_' + str(imdb_id)) == 'available':
                    db.store('movie_' + str(imdb_id), 'available')

                if list.retrieve_key(str(movie_tit)):
                    list.delete(str(movie_tit))

    for r in requested_shows:
        imdb_id = r.get("imdbId")
        show_tit = r.get("title")

        infos = None
        child_requests = r.get("childRequests")
        for cr in child_requests:
            if bool(cr.get("approved")):
                if not bool(cr.get("available")):
                    details = cr.get("seasonRequests")
                    for season in details:
                        # counter for episodes
                        searchepisodes = 0
                        sn = season.get("seasonNumber")
                        eps = []
                        episodes = season.get("episodes")
                        s = str(sn)
                        if len(s) == 1:
                            s = "0" + s
                        s = "S" + s
                        show_tits = generate_reg_title(
                            show_tit, s, sjquality)
                        mbshow_tits = generate_reg_title(
                            show_tit, s, mbquality)

                        for episode in episodes:
                            if not bool(episode.get("available")):
                                searchepisodes += 1
                                enr = episode.get("episodeNumber")
                                e = str(enr)
                                if len(e) == 1:
                                    e = "0" + e
                                se = s + "E" + e

                                if db.retrieve('show_' + str(imdb_id) + '_' + se) == 'added':
                                    db.delete(
                                        'show_' + str(imdb_id) + '_' + se)
                                    db.store('show_' + str(imdb_id) +
                                             '_' + se, 'search')
                                    eps.append(enr)

                                elif not db.retrieve('show_' + str(imdb_id) + '_' + se) == 'search':
                                    db.store('show_' + str(imdb_id) +
                                             '_' + se, 'search')
                                    eps.append(enr)

                                if db.retrieve('show_' + str(imdb_id) + '_' + se) == 'search':
                                    show_titse = generate_reg_title(
                                        show_tit, se, sjquality)
                                    show_tit_search = generate_api_title(
                                        show_tit, s)

                                    if sjregex == True:
                                        if not sjregexdb.retrieve_key(show_titse):
                                            sjregexdb.store_key(show_titse)
                                            print(u"Episode " + show_titse +
                                                  u" zu Regex hinzugefuegt.")

                            elif bool(episode.get("available")):
                                enr = episode.get("episodeNumber")
                                e = str(enr)
                                if len(e) == 1:
                                    e = "0" + e
                                se = s + "E" + e

                                if db.retrieve('show_' + str(imdb_id) + '_' + se) == 'added':
                                    db.delete(
                                        'show_' + str(imdb_id) + '_' + se)
                                    db.store('show_' + str(imdb_id) +
                                             '_' + se, 'available')

                                elif db.retrieve('show_' + str(imdb_id) + '_' + se) == 'search':
                                    db.delete(
                                        'show_' + str(imdb_id) + '_' + se)
                                    db.store('show_' + str(imdb_id) +
                                             '_' + se, 'available')

                                elif not db.retrieve('show_' + str(imdb_id) + '_' + se) == 'available':
                                    db.store('show_' + str(imdb_id) +
                                             '_' + se, 'available')

                                if db.retrieve('show_' + str(imdb_id) + '_' + se) == 'available':
                                    show_titse = generate_reg_title(
                                        show_tit, se, sjquality)

                                    if sjregex == True:
                                        if sjregexdb.retrieve_key(show_titse):
                                            sjregexdb.delete(show_titse)
                                            print(u"Episode " + show_titse +
                                                  u" von Regex entfernt.")

                        if searchepisodes < 2:
                            if sjregex == True:
                                if sjregexdb.retrieve_key(show_tits):
                                    sjregexdb.delete(show_tits)
                                    print(u"Staffel " + show_tits +
                                          u" von SJ Regex entfernt.")

                            if mbregex == True and mbseasons == True:
                                if mbregexdb.retrieve_key(mbshow_tits):
                                    mbregexdb.delete(mbshow_tits)
                                    print(u"Staffel " + mbshow_tits +
                                          u" von MB Regex entfernt.")

                        elif searchepisodes > 3:
                            if sjregex == True:
                                if not sjregexdb.retrieve_key(show_tits):
                                    sjregexdb.store_key(show_tits)
                                    print(u"Staffel " + show_tits +
                                          u" zu SJ-Regex hinzugefuegt.")

                            if mbregex == True and mbseasons == True:
                                if not mbregexdb.retrieve_key(mbshow_tits):
                                    mbregexdb.store_key(mbshow_tits)
                                    print(u"Staffel " + mbshow_tits +
                                          u" zu MB-Regex hinzugefuegt.")

                        searchepisodes = 0

                        if eps:
                            if not infos:
                                infos = imdb_show(
                                    imdb_id, configfile, dbfile, scraper)
                            if infos:
                                title = infos[0]
                                all_eps = infos[1]
                                scraper = infos[2]
                                check_sn = False
                                if all_eps:
                                    check_sn = all_eps.get(sn)
                                if check_sn:
                                    sn_length = len(eps)
                                    check_sn_length = len(check_sn)
                                    if check_sn_length > sn_length:
                                        for ep in eps:
                                            e = str(ep)
                                            if len(e) == 1:
                                                e = "0" + e
                                            se = s + "E" + e
                                            payload = search.best_result_sj(
                                                title, configfile, dbfile)
                                            if payload:
                                                payload = decode_base64(
                                                    payload).split("|")
                                                payload = encode_base64(
                                                    payload[0] + "|" + payload[1] + "|" + se)
                                                added_episode = search.download_sj(
                                                    payload, configfile, dbfile)
                                                if not added_episode:
                                                    payload = decode_base64(
                                                        payload).split("|")
                                                    payload = encode_base64(
                                                        payload[0] + "|" + payload[1] + "|" + s)
                                                    add_season = search.download_sj(
                                                        payload, configfile, dbfile)
                                                    for e in eps:
                                                        e = str(e)
                                                        if len(e) == 1:
                                                            e = "0" + e
                                                        se = s + "E" + e

                                                        if db.retrieve('show_' + str(imdb_id) + '_' + se) == 'added':
                                                            db.delete(
                                                                'show_' + str(imdb_id) + '_' + se)
                                                            db.store(
                                                                'show_' + str(imdb_id) + '_' + se, 'search')
                                                        elif not db.retrieve('show_' + str(imdb_id) + '_' + se) == 'search':
                                                            db.store(
                                                                'show_' + str(imdb_id) + '_' + se, 'search')

                                                    if not add_season:
                                                        log_debug(
                                                            u"Konnte kein Release für " + title + " " + se + "finden.")
                                                    break
                                            if not db.retrieve('show_' + str(imdb_id) + '_' + se) == 'search':
                                                db.store(
                                                    'show_' + str(imdb_id) + '_' + se, 'search')
                                    else:
                                        payload = search.best_result_sj(
                                            title, configfile, dbfile)
                                        if payload:
                                            payload = decode_base64(
                                                payload).split("|")
                                            payload = encode_base64(
                                                payload[0] + "|" + payload[1] + "|" + s)
                                            search.download_sj(
                                                payload, configfile, dbfile)
                                        for ep in eps:
                                            e = str(ep)
                                            if len(e) == 1:
                                                e = "0" + e
                                            se = s + "E" + e

                                            if db.retrieve('show_' + str(imdb_id) + '_' + se) == 'added':
                                                db.delete(
                                                    'show_' + str(imdb_id) + '_' + se)
                                                db.store(
                                                    'show_' + str(imdb_id) + '_' + se, 'search')
                                            elif not db.retrieve('show_' + str(imdb_id) + '_' + se) == 'search':
                                                db.store(
                                                    'show_' + str(imdb_id) + '_' + se, 'search')

                                    print(u"Serie/Staffel/Episode: " +
                                          title + u" durch Ombi hinzugefügt.")

                else:
                    details = cr.get("seasonRequests")
                    for season in details:
                        searchepisodes = 0
                        sn = season.get("seasonNumber")
                        eps = []
                        episodes = season.get("episodes")
                        
                        s = str(sn)
                        if len(s) == 1:
                            s = "0" + s
                        s = "S" + s

                        show_tits = generate_reg_title(
                            show_tit, s, sjquality)
                        mbshow_tits = generate_reg_title(
                            show_tit, s, mbquality)
                        for episode in episodes:
                            # Datenbank erweiterung ok
                            if bool(episode.get("available")):
                                searchepisodes += 1
                                enr = episode.get("episodeNumber")

                                e = str(enr)
                                if len(e) == 1:
                                    e = "0" + e
                                se = s + "E" + e
                                if db.retrieve('show_' + str(imdb_id) + '_' + se) == 'added':
                                    db.delete(
                                        'show_' + str(imdb_id) + '_' + se)
                                    db.store('show_' + str(imdb_id) +
                                             '_' + se, 'available')

                                elif db.retrieve('show_' + str(imdb_id) + '_' + se) == 'search':
                                    db.delete(
                                        'show_' + str(imdb_id) + '_' + se)
                                    db.store('show_' + str(imdb_id) +
                                             '_' + se, 'available')

                                elif not db.retrieve('show_' + str(imdb_id) + '_' + se) == 'available':
                                    db.store('show_' + str(imdb_id) +
                                             '_' + se, 'available')

                                if db.retrieve('show_' + str(imdb_id) + '_' + se) == 'available':
                                    show_titse = generate_reg_title(
                                        show_tit, se, sjquality)

                                    if sjregex == True:
                                        if sjregexdb.retrieve_key(show_titse):
                                            sjregexdb.delete(show_titse)
                                            print(u"Episode " + show_titse +
                                                  u" von Regex entfernt.")

                        if searchepisodes > 3:
                            if sjregex == True:
                                if sjregexdb.retrieve_key(show_tits):
                                    sjregexdb.delete(show_tits)
                                    print(u"Staffel " + show_tits +
                                          u" von SJ Regex entfernt.")

                            if mbregex == True and mbseasons == True:
                                if mbregexdb.retrieve_key(mbshow_tits):
                                    mbregexdb.delete(mbshow_tits)
                                    print(u"Staffel " + mbshow_tits +
                                          u" von MB Regex entfernt.")

                        elif searchepisodes < 2:
                            if sjregex == True:
                                if not sjregexdb.retrieve_key(show_tits):
                                    sjregexdb.store_key(show_tits)
                                    print(u"Staffel " + show_tits +
                                          u" zu SJ-Regex hinzugefuegt.")

                            if mbregex == True and mbseasons == True:
                                if not mbregexdb.retrieve_key(mbshow_tits):
                                    mbregexdb.store_key(mbshow_tits)
                                    print(u"Staffel " + mbshow_tits +
                                          u" zu MB-Regex hinzugefuegt.")
    return device
