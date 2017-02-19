#!/usr/bin/python

#
# Written by Serhii aka Nucleusis
# Many thanks to Rick Spencer <rick.spencer@canonical.com>
# and Stein Magnus Jodal <stein.magnus@jodal.no>
# for the python MPRIS D-Bus implementation
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3, as published
# by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranties of
# MERCHANTABILITY, SATISFACTORY QUALITY, or FITNESS FOR A PARTICULAR
# PURPOSE.  See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program.  If not, see <http://www.gnu.org/licenses/>.
#

"""Cmus Ubuntu Sound Menu integration.

*** Installation ***

In order for a media player to appear in the sonud menu, it must have
a desktop file in /usr/share/applications. For a cmus player,
there must be desktop file /usr/share/applications/cmus.desktop
For example, cmus.desktop might look like the follwing:
[Desktop Entry]
Name=C* Music Player
Comment=cmus - small, fast and powerful console music player
Keywords=audio,player,music
Exec=env TERM=xterm-256color cmus
Terminal=true
Type=Application
Icon=multimedia-player
Categories=Audio;Player;
NoDisplay=false
MimeType=audio/mpeg; audio/x-mp3; audio/x-mpeg; audio/x-musepack; audio/x-wavpack; application/ogg; audio/x-ogg; audio/aac; audio/aacp; x-content/audio-cdda; application/x-cue;

To use this script you must install some dependencies:
$ sudo apt-get install python-gi python-dbus python-pil python-mutagen

Download the script to a convenient location (e.g. ~/.cmus),
ensure it is executable.
Set cmus_soundmenu.py as status_display_program. For expmple, in cmus type
:set status_display_program=/path/to/location/cmus_soundmenu.py

Configuring
You can change options NOTIFICATIONS_ENABLE, COVER_IMAGE_ENABLE,
SOUNDMENU_ENABLE in SETTINGS section below.
Replace True to False in order to diasble an option
"""

from __future__ import unicode_literals

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GObject
from subprocess import Popen, PIPE
import base64
import sys
import os
import logging

from PIL import Image
from io import BytesIO
from mutagen import File
from tempfile import NamedTemporaryFile

from gi.repository import Notify

# --- SETTINGS ---
NOTIFICATIONS_ENABLE = True     # desktop notifications
COVER_IMAGE_ENABLE = True
SOUNDMENU_ENABLE = True         # MPRIS D-Bus service
# ----------------


class CmusSoundMenu(dbus.service.Object):
    """
    Provides Sound Menu integration via limited MPRIS2 service implementation,
    interacts with cmus via cmus-remote and status_display_program,
    shows desktop notifications via Notify,
    finds, processes and uses cover images
    """
    DESKTOP_NAME = 'cmus'
    SCRIPT_NAME = DESKTOP_NAME + '.soundmenu'

    def __init__(self, loop):
        """
        Creates a CmusSoundMenu object.
        Requires a dbus loop to be created before the gtk mainloop,
        typically by calling DBusGMainLoop(set_as_default=True).
        argument loop - MainLoop from GObject
        """

        self.loop = loop
        if NOTIFICATIONS_ENABLE:
            try:
                Notify.init("cmus_soundmenu")
            except Exception as e:
                logging.debug("cannot init Notify: " + str(e))
        if COVER_IMAGE_ENABLE:
            self.tempimage = NamedTemporaryFile()
        self.status = None
        self.get_status()
        self.show_notification(self.status)
        self._set_init_properties()
        if SOUNDMENU_ENABLE:
            bus_str = "org.mpris.MediaPlayer2.%s" % (self.SCRIPT_NAME)
            bus_name = dbus.service.BusName(bus_str, bus=dbus.SessionBus())
            dbus.service.Object.__init__(self, bus_name,
                                         "/org/mpris/MediaPlayer2")
        else:
            GObject.timeout_add(0, self.quit_script)

    def quit_script(self):
        logging.debug("Quit script")
        if SOUNDMENU_ENABLE:
            self.remove_from_connection()
            logging.debug("D-Bus connection closed")
        if self.loop.is_running():
            self.loop.quit()
            logging.debug("loop closed")
        if COVER_IMAGE_ENABLE:
            try:
                self.tempimage.close()
                logging.debug("tempfile closed")
            except Exception as e:
                logging.debug("cannot close tempfile: " + str(e))
        if NOTIFICATIONS_ENABLE:
            try:
                Notify.uninit()
            except Exception as e:
                logging.debug("cannot uninit Notify: " + str(e))

    def cmus_command(self, command):
        """control cmus via cmus-remote commands"""
        logging.debug("cmus-remote " + command)
        process = Popen("cmus-remote " + command,
                        stdout=PIPE, stderr=PIPE, shell=True)
        process.wait()
        stdout, stderr = process.communicate()
        if len(stderr) > 0:
            logging.debug("cmus-remote stderr: " + str(stderr))
            GObject.timeout_add(0, self.quit_script)
        return stdout.decode('utf-8')

    def set_status(self, raw_status):
        """sets status inside the script"""
        if raw_status == "" or raw_status.startswith("cmus-remote"):
            new_status = None
            logging.debug("bad status")
        else:
            obligatory = ('status',
                          'duration',
                          'continue',
                          'repeat',
                          'repeat_current',
                          'shuffle',
                          'vol_left',
                          'vol_right')
            new_status = {}
            new_status['title'] = ''
            new_status['file'] = ''
            if self.status is None:
                for key in obligatory:
                    new_status[key] = ''
            else:
                for key in obligatory:
                    new_status[key] = self.status[key]
            for line in raw_status.splitlines():
                key, value = line.split(" ", 1)
                if key in ("tag", "set"):
                    value = value.split(" ", 1)
                    new_status[value[0]] = value[1]
                else:
                    new_status[key] = value
            new_status['title'] = self.get_title(new_status['title'],
                                                 new_status['file'])
            cover = self.get_cover(new_status)
            if cover is not None:
                new_status['cover'] = cover
        if self.status is not None:
            self._status_changed(new_status)
        self.status = new_status
        logging.debug("Status: " + str(self.status))

    def get_status(self):
        """takes status from cmus"""
        self.set_status(self.cmus_command("-Q"))

    def _set_init_properties(self):
        """set properties after initialization """
        root_properties = {
            'CanQuit':          True,
            'Fullscreen':       False,
            'CanSetFullscreen': False,
            'CanRaise':         False,
            'HasTrackList':     False,
            'Identity':         self.DESKTOP_NAME,
            'DesktopEntry':     self.DESKTOP_NAME,
            'SupportedUriSchemes': dbus.Array([
                dbus.String('file'),
                dbus.String('http'),
                dbus.String('cue'),
                dbus.String('cdda'),
                ], signature='s'),
            'SupportedMimeTypes': dbus.Array([
                dbus.String('audio/mpeg'),
                dbus.String('audio/x-mp3'),
                dbus.String('audio/x-mpeg'),
                dbus.String('audio/x-musepack'),
                dbus.String('audio/x-wavpack'),
                dbus.String('application/ogg'),
                dbus.String('audio/x-ogg'),
                dbus.String('audio/aac'),
                dbus.String('audio/aacp'),
                dbus.String('x-content/audio-cdda'),
                dbus.String('application/x-cue'),
                ], signature='s')
        }
        player_properties = {
            'PlaybackStatus':   self.get_PlaybackStatus(self.status),
            'LoopStatus':       self.get_LoopStatus(self.status),
            'Rate':             1.0,
            'Shuffle':          self.get_Shuffle(self.status),
            'Metadata':         self.get_Metadata(self.status),
            'Volume':           self.get_Volume(self.status),
            'Position':         self.get_Position(self.status),
            'MinimumRate':      1.0,
            'MaximumRate':      1.0,
            'CanGoNext':        True,
            'CanGoPrevious':    True,
            'CanPlay':          True,
            'CanPause':         True,
            'CanSeek':          True,
            'CanControl':       True
        }
        playlists_properties = {
            # 'PlaylistCount':    self.get_PlaylistCount,
            # 'Orderings':        self.get_Orderings,
            # 'ActivePlaylist':   self.get_ActivePlaylist,
        }
        tracklist_properties = {
            # 'Tracks':           self.get_Tracks,
            # 'CanEditTracks':    False
        }
        self.properties = {
            'org.mpris.MediaPlayer2':              root_properties,
            'org.mpris.MediaPlayer2.Player':       player_properties,
            'org.mpris.MediaPlayer2.Playlists':    playlists_properties,
            'org.mpris.MediaPlayer2.TrackList':    tracklist_properties
        }

    # --- Properties interface (org.freedesktop.DBus.Properties)

    @dbus.service.method(dbus.PROPERTIES_IFACE,
                         in_signature='ss', out_signature='v')
    def Get(self, interface, prop):
        """Get dbus properties """
        logging.debug(
            '%s.Get(%s, %s) called',
            dbus.PROPERTIES_IFACE, repr(interface), repr(prop))
        return self.properties[interface][prop]

    @dbus.service.method(dbus.PROPERTIES_IFACE, in_signature='ssv')
    def Set(self, interface, prop, value):
        """Set dbus properties"""
        logging.debug(
            '%s.Set(%s, %s, %s) called',
            dbus.PROPERTIES_IFACE, repr(interface), repr(prop), repr(value))

        if interface == 'org.mpris.MediaPlayer2.Player':
            set_dict = {
                'LoopStatus':   self.set_LoopStatus,
                'Shuffle':      self.set_Shuffle,
                'Volume':       self.set_Volume
            }
            if prop in set_dict:
                set_dict[prop](value)
        self.get_status()

    @dbus.service.method(dbus.PROPERTIES_IFACE,
                         in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface):
        """GetAll dbus properties"""
        logging.debug(
            '%s.GetAll(%s) called', dbus.PROPERTIES_IFACE, repr(interface))
        return self.properties[interface]

    @dbus.service.signal(dbus_interface=dbus.PROPERTIES_IFACE,
                         signature='sa{sv}as')
    def PropertiesChanged(self, interface, changed_properties,
                          invalidated_properties):
        logging.debug(
            '%s.PropertiesChanged(%s, %s, %s) signaled',
            dbus.PROPERTIES_IFACE, interface, changed_properties,
            invalidated_properties)
        for prop in changed_properties:
            self.properties[interface][prop] = changed_properties[prop]

    def _status_changed(self, new_status):
        """checks changes in status"""
        changed = set([])
        if new_status is not None:
            if self.status is None:
                changed = set([
                    'PlaybackStatus',
                    'LoopStatus',
                    'Shuffle',
                    'Metadata',
                    'Volume'])
            else:
                status_dict = {
                    'status':           'PlaybackStatus',
                    'continue':         'LoopStatus',
                    'repeat':           'LoopStatus',
                    'repeat_current':   'LoopStatus',
                    'shuffle':          'Shuffle',
                    'vol_left':         'Volume',
                    'vol_right':        'Volume',
                    'artist':           'Metadata',
                    'title':            'Metadata',
                    'album':            'Metadata'
                }
                for key in status_dict:
                    if key in new_status:
                        if key not in self.status:
                            changed.add(status_dict[key])
                        elif self.status[key] != new_status[key]:
                            changed.add(status_dict[key])
        if len(changed) > 0:
            if 'Metadata' in changed:  # or 'PlaybackStatus' in changed:
                self.show_notification(new_status)
            prop_dict = {
                    'PlaybackStatus':   self.get_PlaybackStatus,
                    'LoopStatus':       self.get_LoopStatus,
                    'Shuffle':          self.get_Shuffle,
                    'Metadata':         self.get_Metadata,
                    'Volume':           self.get_Volume
            }
            new_properties = {}
            for prop in changed:
                new_properties[prop] = prop_dict[prop](new_status)
            self.PropertiesChanged('org.mpris.MediaPlayer2.Player',
                                   new_properties, [])

    # --- Root interface methods (org.mpris.MediaPlayer2)

    @dbus.service.method('org.mpris.MediaPlayer2')
    def Raise(self):
        """
        Bring the media player to the front
        when selected by the sound menu
        """
        logging.debug('%s.Raise called', 'org.mpris.MediaPlayer2')
        raise NotImplementedError("""@dbus.service.method('org.mpris.MediaPlayer2') Raise
                                      is not implemented by this player.""")
        self._sound_menu_raise()

    @dbus.service.method('org.mpris.MediaPlayer2')
    def Quit(self):
        """Causes the media player to stop running"""
        logging.debug('%s.Quit called', 'org.mpris.MediaPlayer2')
        self.cmus_command("-C q")
        GObject.timeout_add(0, self.quit_script)

    @dbus.service.method('org.mpris.MediaPlayer2', in_signature='s')
    def SetStatus(self, arg):
        """receives status info from another instance of the script"""
        logging.debug("got the status from another instance")
        if len(arg) > 0:
            self.set_status(arg)

    # --- Player interface properties

    def get_PlaybackStatus(self, new_status):
        """returns the current playback status"""
        playback_value = 'Stopped'
        playback_dict = {
            'playing':  'Playing',
            'paused':   'Paused',
            'stopped':  'Stopped'
        }
        if new_status is not None and "status" in new_status:
            if new_status['status'] in playback_dict:
                playback_value = playback_dict[new_status['status']]
        return playback_value

    def get_LoopStatus(self, new_status):
        """returns the current loop / repeat status"""
        if new_status is None:
            return ''
        if all(('continue' in new_status,
                'repeat' in new_status,
                'repeat_current')):
            if new_status['continue'] == 'false':
                return 'None'
            elif new_status['continue'] == 'true':
                if new_status['repeat_current'] == 'true':
                    return 'Track'
                elif new_status['repeat'] == 'true':
                    return 'Playlist'
                else:
                    return 'None'
        return ''

    def get_Metadata(self, new_status):
        """returns the metadata of the current element"""
        if new_status is None:
            return ''
        metadata = {'mpris:trackid': self.get_track_id(new_status['file'])}
        if 'duration' in new_status and new_status['duration'] != '':
            metadata['mpris:length'] = dbus.Int64(
                                        int(new_status['duration'])*1000)
        if 'cover' in new_status and new_status['cover'] != '':
            metadata['mpris:artUrl'] = self.get_url(new_status['cover'])
        if 'album' in new_status and new_status['album'] != '':
            metadata['xesam:album'] = new_status['album']
        if 'albumartist' in new_status and new_status['albumartist'] != '':
            metadata['xesam:albumArtist'] = dbus.Array(
                new_status['albumartist'].split('/'), signature='s')
        if 'artist' in new_status and new_status['artist'] != '':
            metadata['xesam:artist'] = dbus.Array(
                new_status['artist'].split('/'), signature='s')
        if 'comment' in new_status and new_status['comment'] != '':
            metadata['xesam:comment'] = dbus.Array(
                new_status['comment'].split('\n'), signature='s')
        if 'composer' in new_status and new_status['composer'] != '':
            metadata['xesam:composer'] = dbus.Array(
                new_status['composer'].split('/'), signature='s')
        if 'date' in new_status and new_status['date'] != '':
            metadata['xesam:contentCreated'] = new_status['date']
        if 'discnumber' in new_status and new_status['discnumber'] != '':
            metadata['xesam:discNumber'] = int(new_status['discnumber'])
        if 'genre' in new_status and new_status['genre'] != '':
            metadata['xesam:genre'] = dbus.Array(
                new_status['genre'].split('/'), signature='s')
        if 'title' in new_status and new_status['title'] != '':
            metadata['xesam:title'] = new_status['title']
        if 'tracknumber' in new_status and new_status['tracknumber'] != '':
            metadata['xesam:trackNumber'] = int(new_status['tracknumber'])
        if 'file' in new_status and new_status['file'] != '':
            metadata['xesam:url'] = self.get_url(new_status['file'])
        return dbus.Dictionary(metadata, signature='sv')

    def get_Shuffle(self, new_status):
        """
        returns if playback is progressing through a playlist
        linearly or in some other order
        """
        if new_status is None:
            return False
        if 'shuffle' in new_status and len(new_status['shuffle']) > 0:
            return bool(new_status['shuffle'])
        return False

    def get_Volume(self, new_status):
        """returns the volume level"""
        if new_status is None:
            return 0.0
        if 'vol_left' in new_status and 'vol_right' in new_status:
                volume = sum((int(new_status['vol_left']),
                              int(new_status['vol_right']))) / 200.0
                return volume
        return 0.0

    def get_Position(self, new_status):
        """returns position in current track"""
        if new_status is None:
            return ''
        if 'position' in new_status and len(new_status['position']) > 0:
            return dbus.Int32(int(new_status['position']) * 1000)
        else:
            self.get_status()
            if 'position' in new_status and len(new_status['position']) > 0:
                return int(new_status['position']) * 1000

    # --- set player interface property

    def set_LoopStatus(self, value):
        """sets the current loop / repeat status"""
        if self.status is None:
            return
        if value == 'None':
            self.cmus_command('-C "set continue=false"')
        elif value == 'Track':
            self.cmus_command('-C "set continue=true"')
            self.cmus_command('-C "set repeat_current=true"')
        elif value == 'Playlist':
            self.cmus_command('-C "set continue=true"')
            self.cmus_command('-C "set repeat_current=false"')
            self.cmus_command('-C "set repeat=true"')

    def set_Shuffle(self, value):
        """sets the current shuffle status"""
        if self.status is None:
            return
        if value == 'True':
            self.cmus_command('-C "set shuffle=true"')
        elif value == 'False':
            self.cmus_command('-C "set shuffle=false"')

    def set_Volume(self, value):
        """sets the volume level"""
        if self.status is None:
            return
        value = int(value)
        if value is None:
            return
        elif value < 0:
            self.cmus_command('-v 0%')
        elif value > 1:
            self.cmus_command('-v 100%')
        elif 0 <= value <= 1:
            self.cmus_command('-v %d%' % value)

    # --- helpers for player interface properties

    def encoded_uri(self, uri):
        """transform URI to ID"""
        # Only A-Za-z0-9_ is allowed, which is 63 chars, so we can't use
        # base64. Luckily, D-Bus does not limit the length of object paths.
        # Since base32 pads trailing bytes with "=" chars, we need to replace
        # them with an allowed character such as "_".
        return str(base64.b32encode(uri.encode('utf-8'))).replace('=', '_')

    def get_track_id(self, track_path):
        """returns tack ID based on file path and name"""
        track_id_name = self.encoded_uri(track_path)
        track_id_path = '/com/%s/track/' % self.DESKTOP_NAME
        return track_id_path + track_id_name

    def get_title(self, title, file_name):
        """title of the current track"""
        if title != '':
            return title
        elif file_name != '':
            if '://' in file_name:
                return file_name
            else:
                return os.path.splitext(os.path.basename(file_name))[0]
        else:
            return ''

    def get_url(self, file_name):
        """returns the location of the media file"""
        if '://' in file_name:
            return file_name
        else:
            return 'file://' + file_name

    # --- Player interface methods (org.mpris.MediaPlayer2.Player)

    @dbus.service.method('org.mpris.MediaPlayer2.Player')
    def Next(self):
        """Skips to the next track in the tracklist"""
        logging.debug('%s.Next called', 'org.mpris.MediaPlayer2.Player')
        self.cmus_command("-n")

    @dbus.service.method('org.mpris.MediaPlayer2.Player')
    def Previous(self):
        """Skips to the previous track in the tracklist"""
        logging.debug('%s.Previous called', 'org.mpris.MediaPlayer2.Player')
        self.cmus_command("-r")

    @dbus.service.method('org.mpris.MediaPlayer2.Player')
    def Pause(self):
        """Pauses playback"""
        logging.debug('%s.Pause called', 'org.mpris.MediaPlayer2.Player')
        self.cmus_command("-u")

    @dbus.service.method('org.mpris.MediaPlayer2.Player')
    def PlayPause(self):
        """Starts/resumes or stops playback"""
        logging.debug('%s.PlayPause called', 'org.mpris.MediaPlayer2.Player')
        if self.status is not None and 'status' in self.status:
            playback_value = self.status['status']
        else:
            playback_value = 'stopped'
        if playback_value in ('paused', 'stopped'):
            self.cmus_command("-p")
        elif playback_value == 'playing':
            self.cmus_command("-u")

    @dbus.service.method('org.mpris.MediaPlayer2.Player')
    def Stop(self):
        """Stops playback"""
        logging.debug('%s.Stop called', 'org.mpris.MediaPlayer2.Player')
        self.cmus_command("-s")

    @dbus.service.method('org.mpris.MediaPlayer2.Player')
    def Play(self):
        """Starts or resumes playback"""
        logging.debug('%s.Play called', 'org.mpris.MediaPlayer2.Player')
        self.cmus_command("-p")

    @dbus.service.method('org.mpris.MediaPlayer2.Player')
    def Seek(self, offset):
        """
        Seeks forward in the current track
        by the specified number of microseconds
        """
        logging.debug('%s.Seek called', 'org.mpris.MediaPlayer2.Player')
        self.cmus_command("-k %+d" % (int(offset) // 1000))

    @dbus.service.method('org.mpris.MediaPlayer2.Player')
    def SetPosition(self, track_id, position):
        """Sets the current track position in microseconds"""
        logging.debug('%s.SetPosition called', 'org.mpris.MediaPlayer2.Player')
        position = position // 1000
        if self.status is None:
            return
        if track_id != self.get_track_id(self.status['file']):
            return
        if position < 0:
            return
        if position > self.status['duration']:
            return
        self.cmus_command("-k %d" % (int(position)))

    @dbus.service.method('org.mpris.MediaPlayer2.Player')
    def OpenUri(self, uri):
        """Opens the Uri given as an argument"""
        logging.debug('%s.OpenUri called', 'org.mpris.MediaPlayer2.Player')
        self.cmus_command("-c -q %s" % uri)
        self.cmus_command("-n")

    # --- Player interface signals

    @dbus.service.signal('org.mpris.MediaPlayer2.Player', signature='x')
    def Seeked(self, position):
        logging.debug('%s.Seeked signaled', 'org.mpris.MediaPlayer2.Player')
        # Do nothing, as just calling the method is enough to emit the signal.

    # --- Cover image extraction

    def get_embedded_cover(self, filepath):
        """extracts cover image from audio file"""
        # finds the audio file
        try:
            audio_file = File(filepath)
        except Exception:
            logging.debug("audio file is not suitable for image extraction")
            return
        # searching for the text of an image
        try:
            cover_key = None
            if 'APIC' in audio_file:
                cover_key = 'APIC'
            else:
                for key in audio_file.keys():
                    if key.startswith('APIC:'):
                        cover_key = key
                        break
            if cover_key is not None:
                apic = audio_file.get(cover_key)
                logging.debug("found an embedded image inside audio file")
            else:
                logging.debug("audio file does not have an embedded image")
                return
        except Exception as e:
            logging.debug("error in an embedded image detection: " + str(e))
            return
        # image extraction
        try:
            artwork = BytesIO(apic.data)
            logging.debug("an embedded image has been extracted")
            return artwork
        except Exception as e:
            logging.debug("cannot extract an embedded image: " + str(e))
            return

    def get_dir_cover(self, filepath):
        """search for cover file inside the folder with audio file"""
        try:
            if filepath.startswith('http://'):
                return
            elif filepath.startswith('cue:///'):
                filepath = filepath[6:]
                filepath = os.path.dirname(os.path.abspath(filepath))
            dirpath = os.path.dirname(os.path.abspath(filepath))
            cover_names = (
                    "cover",
                    "front",
                    "bground",
                    "folder",
                    "albumart",
                )
            file_extensions = (".jpg", ".jpeg", ".png")
            coverfiles = os.listdir(dirpath)
            coverfiles = filter(lambda x: x.lower().endswith(file_extensions),
                                coverfiles)
            coverfiles = list(coverfiles)
            if len(coverfiles) == 1:
                cover_names = ('',)
            coverfiles = filter(lambda x: x.lower().startswith(cover_names),
                                coverfiles)
            coverfiles = list(coverfiles)
            coverfiles.sort(key=lambda x: [x.lower().startswith(i)
                                           for i in cover_names].index(True))
            coverpath = None
            for coverfile in coverfiles:
                coverpath = os.path.join(dirpath, coverfile)
                if os.path.exists(coverpath):
                    break
                else:
                    coverpath = None
            if coverpath is not None:
                logging.debug("found image file: " + coverpath)
                return coverpath
            else:
                logging.debug("cover image file not found")
                return
        except Exception as e:
            logging.debug("cannot find image file: " + str(e))
            return

    def get_cover(self, new_status):
        """finds and copies cover image of audio file"""
        if not COVER_IMAGE_ENABLE:
            return
        if new_status is None:
            return
        if 'file' not in new_status:
            return
        if new_status['file'] == '':
            return
        filepath = new_status['file']
        artwork = self.get_embedded_cover(filepath)
        if artwork is None:
            artwork = self.get_dir_cover(filepath)
        if artwork is None:
            logging.debug("cannot show cover image")
            return
        try:
            pic = Image.open(artwork)
            pic = pic.resize((128, 128))
            pic.save(self.tempimage, format="PNG")
            pic.seek(0)
            self.tempimage.seek(0)
            try:
                artwork.close()
            except AttributeError:
                pass
            return self.tempimage.name
        except Exception as e:
            logging.debug("cannot process cover image: " + str(e))
            return

    # --- Desktop notifications

    def show_notification(self, new_status):
        """shows desktop notification with a new status"""
        if not NOTIFICATIONS_ENABLE:
            return
        try:
            if 'title' in new_status:
                header = new_status['title']
            else:
                header = ''
            if 'artist' in new_status:
                msg_artist = new_status['artist']
            else:
                msg_artist = ''
            if 'album' in new_status:
                msg_album = new_status['album']
            else:
                msg_album = ''
            if 'cover' in new_status:
                msg_image = new_status['cover']
            else:
                msg_image = None
            message = msg_artist + '\n' + msg_album
            html_escape_table = {
                "&": "&amp;",
                '"': "&quot;",
                "'": "&#39;",
                ">": "&gt;",
                "<": "&lt;",
                "/": "&#47;",
            }
            message = "".join(html_escape_table.get(c, c) for c in message)
            if header != '':
                notification = Notify.Notification.new(
                    header,
                    message,
                    msg_image,
                )
                notification.set_urgency(0)
                notification.set_timeout(3000)
                notification.show()
        except Exception as e:
            logging.debug("desktop notofication failed: " + str(e))


def another_instance(arg):
    """notifies the another instance of the script"""
    DESKTOP_NAME = 'cmus'
    SCRIPT_NAME = DESKTOP_NAME + '.soundmenu'
    bus_str = "org.mpris.MediaPlayer2.%s" % (SCRIPT_NAME)
    bus = dbus.SessionBus()
    try:
        programinstance = bus.get_object(bus_str,  '/org/mpris/MediaPlayer2')
        setstatus = programinstance.get_dbus_method('SetStatus',
                                                    'org.mpris.MediaPlayer2')
        setstatus(arg)
        logging.info("Another instance was running and notified.")
        return True
    except dbus.exceptions.DBusException as e:
        logging.debug('Check for another instance: ' + str(e))
        return False


def main():
    # turn on the dbus mainloop
    DBusGMainLoop(set_as_default=True)

    # receive arguments
    try:
        arg_list = []
        for param, value in zip(sys.argv[1::2], sys.argv[2::2]):
            arg_list.append(' '.join((param.decode('utf-8'),
                                      value.decode('utf-8'))))
        arg = '\n'.join(arg_list)
    except IndexError:
        arg = ""
    except Exception:
        logging.exception("sys.argv stdin:")
    # check for another instances
    if not another_instance(arg):
        # initiate the main loop
        loop = GObject.MainLoop()
        # initiate a CmusSoundMenu object
        cmus_sound_menu = CmusSoundMenu(loop)
        try:
            # synchronization timer
            GObject.timeout_add(600 * 1000, cmus_sound_menu.get_status)
            logging.debug("loop starts")
            # start the MainLoop
            loop.run()
        except KeyboardInterrupt:
            logging.debug("keyboard interrupt, loop was closed")
            cmus_sound_menu.quit_script()

if __name__ == '__main__':
    logging.basicConfig(
            # filename=os.path.splitext(os.path.abspath(__file__))[0] + ".log",
            # filemode='w',
            # level=logging.DEBUG,
            # level=logging.INFO,
            level=logging.CRITICAL,
            format='%(asctime)s,%(msecs)03d - %(levelname)s - %(message)s',
            datefmt='%a, %d %b %Y %H:%M:%S',
                        )
    logging.info(10 * '-' + 'START' + 10 * '-')
    try:
        main()
        logging.info(9 * '-' + "THE END" + 9 * '-')
    except:
        logging.exception("Oops:")
    logging.shutdown()
