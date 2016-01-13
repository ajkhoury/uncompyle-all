#Embedded file name: C:\src\ide\bin\2.7\src\process\abstract.pyo
""" process/abstract.py -- License manager support

Copyright (c) 1999-2012, Archaeopteryx Software, Inc.  All rights reserved.

"""
import sys
import os
import string
import time
import socket
import tempfile
import random
import marshal
import binascii
import new
import sha
import logging
import urllib
import urllib2
logger = logging.getLogger('general')
from wingutils import destroyable
from wingutils import spawn
from wingutils import location
from wingutils import hostinfo
from wingutils import textio
from wingutils import textutils
from wingutils import fileutils
import config
import gettext
_ = gettext.translation('src_process', fallback=1).ugettext
kHashLicenseFields = ['license',
 'termdays',
 'usercount',
 'product',
 'version',
 'os',
 'restrictions',
 'hostinfo']
kRequiredLicenseFields = kHashLicenseFields + ['date', 'customerdata', 'activation']
kLicenseUseCodes = ['T',
 'N',
 'E',
 'C',
 '1',
 '3',
 '6']
kLicenseProdCodes = {config.kProd101: '1',
 config.kProdPersonal: 'L',
 config.kProdProfessional: 'N',
 config.kProdEnterprise: 'E'}
kLicenseProdCode = kLicenseProdCodes[config.kProductCode]
kLicenseProdForCode = {}
for prod, code in kLicenseProdCodes.items():
    kLicenseProdForCode[code] = prod

kOSRequestCodes = {'win32': 'W',
 'linux': 'L',
 'darwi': 'M',
 'sunos': 'N',
 'freeb': 'F',
 'tru64': 'T',
 'netbs': 'E',
 'openb': 'B'}
kVersionRequestCodes = {'2': 'X',
 '3': '3',
 '4': '4',
 '5': '5'}
kRequestVersionCode = kVersionRequestCodes[config.kVersion[:config.kVersion.find('.')]]
if sys.platform.startswith('linux') and os.uname()[4] in ('ppc', 'ppc64'):
    kRequestPrefix = 'RP' + kRequestVersionCode
else:
    kRequestPrefix = 'R' + kOSRequestCodes[sys.platform[:5]] + kRequestVersionCode
kActivationPrefix = 'AXX'

def LoadFromDat(filename, modname):
    """ Load code from pycontrol.dat file into a module -- this allows us 
    to do some weak code hiding. """
    file = open(filename, 'rb')
    try:
        dict = marshal.load(file)
    finally:
        file.close()

    if type(dict) != type({}):
        raise SystemExit(-1)
    mco = dict.get(sys.hexversion & 4294901760L)
    if mco is None:
        raise SystemExit(-1)
    co = marshal.loads(mco)
    mod = new.module(modname)
    exec co in mod.__dict__
    return mod


if sys.platform[:5] in ('win32', 'darwi') or sys.platform[:5] == 'linux' and os.uname()[4] not in ('ppc', 'ppc64', 'arm7l'):
    import ctlutil as control
else:
    try:
        import pycontrol
        control = pycontrol
    except ImportError:
        dirname = os.path.dirname(__file__).replace('.zip', '')
        control = LoadFromDat(fileutils.join(dirname, 'pycontrol.dat'), 'pycontrol')

try:
    dirname = os.path.dirname(__file__).replace('.zip', '')
    bulkctl = LoadFromDat(fileutils.join(dirname, 'bulkctl.dat'), 'bulkctl')
    bulkctl.init(control)
except:
    bulkctl = None

kLicenseOK = 1
kLicenseNotFound = 2
kLicenseCorrupt = 3
kLicenseExpired = 4
kLicenseTooManyUsers = 5
kLicenseInternalError = 6
kLicenseWrongPlatform = 7
kLicenseWrongProduct = 8
kLicenseHostMismatch = 9

def CreateTrialLicenseDict():
    """ Create license dict with given information for the current
    machine.  The "activation" field is omitted and needs to be
    added in a separate step before writing license to disk.  Note
    that trial licenses are unique to product level and version,
    as well as factoring in machine identity."""
    host = hostinfo.GetHostID()
    shost = hostinfo.SecureHostID(host, (kRequestVersionCode,))
    license = AddHyphens('T' + kLicenseProdCode + 'X' + textutils.SHAToBase30(shost))
    return CreateLicenseDict(license)


def CreateLicenseDict(license):
    """ Create license dict with given information for the current
    machine.  The "activation" field is omitted and needs to be
    added in a separate step before writing license to disk. """
    lic = {}
    vparts = config.kVersion.split('.')
    version = vparts[0] + '.*'
    termdays = '*'
    if license.startswith('6'):
        termdays = 181
        restrictions = 'Limited term use: 180 days'
    elif license.startswith('3'):
        termdays = 91
        restrictions = 'Limited term use: 90 days'
    elif license.startswith('1'):
        termdays = 31
        restrictions = 'Limited term use: 30 days'
    elif license.startswith('T'):
        restrictions = 'Evaluation use only'
        termdays = 11
    elif license.startswith('N'):
        restrictions = 'Unpaid open source or classroom use only'
    elif license.startswith('E'):
        restrictions = 'Non-commercial use  only'
    elif license[1] == 'L':
        restrictions = 'None'
    else:
        restrictions = 'None'
    lic['license'] = license
    lic['date'] = time.strftime('%m/%d/%y', time.localtime(time.time()))
    lic['termdays'] = termdays
    lic['usercount'] = 1
    lic['product'] = config.k_WingideNameMap[config.kProductCode]
    lic['version'] = version
    config._os = ''
    lic['os'] = control.get_os()
    lic['os'] = config._os
    lic['restrictions'] = restrictions
    lic['hostinfo'] = hostinfo.GetHostID()
    lic['customerdata'] = ''
    return lic


def ReadLicenseDict(filename):
    """ Read license file into a dict of values """
    iomgr = textio.CTextIOManager()
    reader = textio.CFileReader(iomgr)
    try:
        try:
            layers = reader.Read(location.CLocalFileLocation(filename))
        except:
            return

        if len(layers) != 1:
            return
        items = layers[0].GetValueItems()
        lic = dict(items)
    finally:
        iomgr.destroy()

    if lic.has_key('hostinfo'):
        hostid = lic['hostinfo']
        try:
            hostid = textutils.BaseConvert(hostid, textutils.BASEMAX, textutils.BASE16).lower()
            hostid = binascii.unhexlify(hostid)
            hostid = marshal.loads(hostid)
        except:
            hostid = {}

        lic['hostinfo'] = hostid
    lic['filename'] = filename
    return lic


def WriteLicenseDict(filename, license, ignore = ()):
    """ Write license file from a dict of values """
    errs = []
    for key in kRequiredLicenseFields:
        if not license.has_key(key) and key not in ignore:
            errs.append(_('License missing required %s') % key)

    license = license.copy()
    hostid = license['hostinfo']
    try:
        hostid = marshal.dumps(hostid)
        hexhostid = binascii.hexlify(hostid).upper()
        license['hostinfo'] = textutils.BaseConvert(hexhostid, textutils.BASE16, textutils.BASEMAX)
    except:
        errs.append(_('Failed to package host information'))

    if len(errs) > 0:
        return errs
    for key in license.keys():
        if key not in kRequiredLicenseFields:
            del license[key]

    lic = license['license']
    if lic[2] in 'LWM':
        lic = RemoveHyphens(lic)
        lic = lic[:10] + 'XXXXXXXXXX'
        license['license'] = AddHyphens(lic)
    header = ('# Wing IDE 3.x license file', '# Editing this file will invalidate your license')
    iomgr = textio.CTextIOManager()
    writer = textio.CFileWriter(iomgr)
    layer = textio.CTextIOValueLayer(iomgr)
    layer.SetValuesFromDict(license)
    try:
        try:
            errs = writer.Write(location.CLocalFileLocation(filename), [layer], header)
        except:
            return ['Exception while writing license file']

        if len(errs) > 0:
            return errs
        os.chmod(filename, 256)
    finally:
        iomgr.destroy()

    return []


def CreateActivationRequest(license):
    """Create hash value from license that can be shipped to the license
    activation server"""
    license = license.copy()
    hostid = license['hostinfo']
    hostid = marshal.dumps(hostid)
    hexhostid = binascii.hexlify(hostid).upper()
    license['hostinfo'] = textutils.BaseConvert(hexhostid, textutils.BASE16, textutils.BASEMAX)
    hasher = sha.new()
    for key in kHashLicenseFields:
        value = license[key]
        if key == 'license' and value[2] not in '123456789' and value.replace('-', '')[10:] == 'XXXXXXXXXX':
            lic = RemoveHyphens(value)
            value = lic[:10] + bulkctl._hash30(lic[:10])[:10]
            value = AddHyphens(value)
        hasher.update(str(value))

    if license['termdays'] != '*':
        hasher.update(str(license['date']))
    digest = hasher.hexdigest().upper()
    return AddHyphens(kRequestPrefix + textutils.SHAToBase30(digest))


def AddHyphens(code):
    """Insert hyphens into given license id or activation request to
    make it easier to read"""
    return code[:5] + '-' + code[5:10] + '-' + code[10:15] + '-' + code[15:]


def RemoveHyphens(code):
    """Remove hyphens from given license id or activation request"""
    code = code.replace('-', '')
    return code


def __ValidateAndNormalize(code):
    """Remove hyphens and extra space/chars in a license id or activation
    request, and validate it as within the realm of possibility.  Returns
    errs, value."""
    errs = []
    code = code.strip().upper()
    code2 = ''
    badchars = set()
    for c in code:
        if c in ('-', ' ', '\t'):
            pass
        elif c not in textutils.BASE30:
            code2 += c
            badchars.add(c)
        else:
            code2 += c

    if badchars:
        try:
            badchars = ''.join(badchars)
        except:
            badchars = '<could not decode>'

        errs.append(_('Contains invalid characters: %s') % str(badchars))
    if len(code2) != 20:
        errs.append(_('Wrong length (should contain 20 non-hyphen characters)'))
    if len(errs) > 0:
        return (errs, code2)
    else:
        return ([], AddHyphens(code2))


def ValidateAndNormalizeLicenseID(id):
    errs, id2 = __ValidateAndNormalize(id)
    if len(id2) > 0 and id2[0] not in kLicenseUseCodes:
        errs.append(_('Invalid first character: Should be one of %s') % str(kLicenseUseCodes))
    if len(id2) > 1 and id2[1] != kLicenseProdCode:
        cur_product = 'Wing IDE %s' % config.kProduct
        lic_product = kLicenseProdForCode.get(id2[1], None)
        if lic_product is None:
            lic_product = _('an unknown product')
        else:
            lic_product = 'Wing IDE %s' % config.k_ProductNames[lic_product]
        errs.append(_('Your license is for %s, but you are currently running %s.  Please download the correct product from http://wingware.com/downloads or upgrade your license at https://wingware.com/store/upgrade') % (lic_product, cur_product))
    if len(errs) > 0:
        check_code = id.strip().upper().replace('-', '')
        if len(check_code) == 16:
            looks_like_11 = True
            for c in check_code:
                if c not in '0123456789ABCDEF':
                    looks_like_11 = False

            if looks_like_11:
                errs = [_('You cannot activate using a Wing IDE 1.1 license:  Please use a trial license or upgrade your license at http://wingware.com/store/upgrade')]
    if len(errs) > 0:
        return (errs, None)
    else:
        return ([], id2)


def ValidateAndNormalizeRequest(id):
    errs, id2 = __ValidateAndNormalize(id)
    if len(errs) == 0:
        if id2[0] != 'R':
            errs.append(_('Request code should start with R'))
        if id2[1] not in kOSRequestCodes.values():
            errs.append(_('Invalid second character:  Should be one of %s') % str(kOSRequestCodes.values()))
        if id2[2] not in kVersionRequestCodes.values():
            errs.append(_('Invalid third character:  Should be one of %s') % str(kVersionRequestCodes.values()))
    if len(errs) > 0:
        return (errs, None)
    else:
        return ([], id2)


def ValidateAndNormalizeActivation(id):
    errs, id2 = __ValidateAndNormalize(id)
    if id2[:3] != kActivationPrefix:
        errs.append(_("Invalid prefix:  Should be '%s'") % kActivationPrefix)
    if len(errs) > 0:
        return (errs, None)
    else:
        return ([], id2)


class CLicenseManager(destroyable.CDestroyable):
    kWebAPIKey = 'AE7B2181D1B3E4657F2AD63E17708BE8'

    def __init__(self):
        """ Constructor """
        destroyable.CDestroyable.__init__(self, ('license-ok',))
        self.fLicenseData = None
        self.fLicenseFile = None
        self.__fLicense = None
        self._fStartTime = time.time()
        self._fSteamOK = False
        self._fSteamUserID = None
        if config.kSteam:
            from wingutils import steam
            if steam.InitializeSteam():
                self._fSteamUserID = steam.GetSteamUserID()
                print 'STEAM APP ID', config.kSteamAppID
                print 'STEAM USER ID', self._fSteamUserID
                steam.ShutdownSteam()
            else:
                print 'STEAM INITIALIZE FAILED'

    def _destroy_impl(self):
        """ Explicit destructor (needed to break circular refs) """
        self.__ReleaseLicense()

    def LicenseOK(self):
        """ Convenience function for checking license at key points.  Returns
        1 if OK and 0 if not but not detailed info """
        if config.kAvail101Support:
            return 1
        if config.kSteam:
            return self.__SteamCheck()
        if self.__fLicense == None:
            return 0
        return 1

    def __SteamCheck(self):
        """Checks for Steam user ownership of this app"""
        if self._fSteamOK:
            return 1
        if self._fSteamUserID is None:
            return 0
        if config.kSteamAppID is None:
            return 0
        kUrl = 'http://api.steampowered.com/ISteamUser/CheckAppOwnership/v0001/?key=%s&appid=%s&steamid=%i' % (self.kWebAPIKey, config.kSteamAppID, self._fSteamUserID)
        try:
            svc = urllib2.urlopen(kUrl)
            txt = svc.read()
            svc.close()
        except:
            return 0

        lines = txt.splitlines()
        for line in lines:
            line = line.lower()
            if line.find('ownsapp') >= 0:
                if line.find('true'):
                    self._fSteamOK = 1
                    return 1
                break

        return 0

    def UseLicense(self, filename):
        """ Checks for valid license contained in the given filename.  If license
        is found and is valid, it is set as the license to use.  Returns status, info
        tuple.
        
        The second part of the returned tuple is extra error info.  Currently
        this is only used for some error messages as follows (None for those
        not listed).
        
        kLicenseOK            -- Tuple of users currently using the license, 
                                 in addition to the this user:  (host, 
                                 uid, user name, process id)
        
        kLicenseTooManyUsers  -- Same
        
        kLicenseCorrupt       -- String with detail
        
        kLicenseWrongPlatform -- String with detail
        
        kLicenseWrongProduct  -- String with detail
        
        """
        lic = ReadLicenseDict(filename)
        if lic == None:
            return (kLicenseNotFound, None)
        status = self.__GrabLicense(lic, filename)
        return status

    def _StatusToErrString(self, status):
        """ Convert status indicator to list of error strings for user """
        retval = []
        if status[0] == kLicenseOK:
            retval.append(_('License is valid.'))
            self.__AppendUserInfo(status, retval)
        elif status[0] == kLicenseNotFound:
            retval.append(_('License file not found or could not be read.'))
        elif status[0] == kLicenseCorrupt:
            retval.append(_('Activation key not valid for this license:'))
            retval.append(status[1])
        elif status[0] == kLicenseExpired:
            retval.append(_('The license term has expired.'))
        elif status[0] == kLicenseTooManyUsers:
            retval.append(_('The maximum number of users for this license has been reached.'))
            self.__AppendUserInfo(status, retval)
        elif status[0] == kLicenseWrongPlatform:
            retval.append(_('License OS does not match current OS.'))
            retval.append(status[1])
        elif status[0] == kLicenseWrongProduct:
            retval.append(_('License does not match this product or product level.'))
            retval.append(status[1])
        elif status[0] == kLicenseHostMismatch:
            retval.append(_("License does not match this host's identity."))
        elif status[0] == kLicenseInternalError:
            retval.append(_('An internal error occurred:'))
            retval.append(status[1])
        else:
            print 'UNKNOWN LICENSE ERR', status
            import traceback
            traceback.print_stack()
            retval.append(_('Unknown error'))
        return retval

    def __AppendUserInfo(self, status, retval):
        """ Add user info list to given string; should only be called with
        status kLicenseOK or kLicenseTooManyUsers """
        if len(status[1]) > 0:
            retval.append(_('Other users are:'))
            for host, ipaddr, uid, name, pid in status[1]:
                retval.append(_('(%s) on host %s (%s) process id %s') % (uid,
                 host,
                 ipaddr,
                 pid))

        else:
            retval.append(_('No other users at this time.'))
        return retval

    def ValidateLicense(self, filename):
        """ Checks the license in given file for validity and availability """
        if not os.path.isfile(filename):
            return (kLicenseNotFound, None)
        lic = ReadLicenseDict(filename)
        if lic == None:
            return (kLicenseNotFound, None)
        license_check = self._ValidateLicenseDict(lic, filename)
        return license_check

    def _ValidatePlatform(self, license, license_os):
        """ Check license os; by default we require oss to match
        but descendents can override.  Should return (err, msg) tuple. """
        if license[2] == 'L':
            license_os = 'linux'
        elif license[2] == 'W':
            license_os = 'windows'
        elif license[2] == 'M':
            license_os = 'macosx'
        config._os = ''
        cur_os = control.get_os()
        cur_os = config._os
        if cur_os == 'INVALID':
            return (kLicenseWrongPlatform, _("Current OS '%s' is not supported") % cur_os)
        if string.find(string.lower(cur_os), string.lower(license_os)) != 0:
            return (kLicenseWrongPlatform, _("License OS '%s' does not match current OS '%s'") % (license_os, cur_os))
        return (None, None)

    def _ValidateProduct(self, license_product):
        """ Check license product: By default we just accept it but descendents 
        can override this. Should return (err, msg) tuple. """
        return (None, None)

    def _ValidateVersion(self, license_version):
        """ Check license version: By default we just accept it but descendents 
        can override this. Should return (err, msg) tuple. """
        return (None, None)

    def __GetSteamTermDaysLeft(self):
        """Get number of days left on a Steam license"""
        if config.kSteamAppID in (244830, 282240):
            return -1
        kUrl = 'http://api.steampowered.com/ISteamUserStats/GetUserStatsForGame/v0001/?key=%s&appid=%s&steamid=%i' % (self.kWebAPIKey, config.kSteamAppID, self._fSteamUserID)
        try:
            svc = urllib2.urlopen(kUrl)
            txt = svc.read()
            svc.close()
        except:
            return 0

        start_time = 0
        found_start = False
        for line in txt.splitlines():
            if line.find('start_time') >= 0:
                found_start = True
            elif found_start:
                parts = line.split(':')
                try:
                    start_time = int(parts[1].strip())
                    break
                except:
                    found_start = False

        if start_time == 0:
            start_time = int(time.time())
            kUrl = 'http://api.steampowered.com/ISteamUserStats/SetUserStatsForGame/v0001'
            data = [('key', self.kWebAPIKey),
             ('appid', config.kSteamAppID)('steamid', self._fSteamUserID),
             ('count', 1),
             ('name[0]', 'start_time'),
             ('value[0]', start_time)]
            data = urllib.urlencode(data)
            try:
                svc = urllib2.urlopen(kUrl, data)
                txt = svc.read()
                svc.close()
            except:
                return 0

        seconds_used = time.time() - start_time
        days_left = 30 - seconds_used * 60 * 60 * 24
        if days_left < 0:
            days_left = 0
        return days_left

    def _GetTermDaysLeft(self, lic = None):
        """ Get number of days left on license.  Returns the days, or 0 if expired,
        -1 if unlimited, or -2 on error """
        if config.kSteam:
            try:
                return self.__GetSteamTermDaysLeft()
            except:
                return -2

        if lic is None:
            lic = self.fLicenseData
        if lic is None:
            return 0
        elif lic['termdays'] != '*':
            try:
                fields = string.split(lic['date'], '/')
                if len(fields) != 3:
                    raise ValueError
                m, d, y = map(string.atoi, fields)
                if m < 1 or m > 12 or d < 1 or d > 31 or y < 0:
                    raise ValueError
                if y < 100:
                    y = 2000 + y
                lic_date = time.mktime((y,
                 m,
                 d,
                 0,
                 0,
                 0,
                 0,
                 0,
                 -1))
            except (ValueError, TypeError, OverflowError):
                return -2

            cur_date = time.time()
            try:
                lic_secs = int(lic['termdays']) * 24 * 60 * 60
            except ValueError:
                return -2

            if cur_date > lic_date + lic_secs:
                return 0
            if lic_date > cur_date + 86400:
                return 0
            return int((lic_secs - (cur_date - lic_date)) / 86400)
        else:
            return -1

    def _ValidateLicenseDict(self, lic, filename):
        """ Check license for internal integrity and expiration """
        lic['daysleft'] = _('expired')
        for key in kRequiredLicenseFields:
            if not lic.has_key(key):
                return (kLicenseCorrupt, _('Missing a required line %s') % key)

        err, msg = self._ValidatePlatform(lic['license'], lic['os'])
        if err != None:
            return (err, msg)
        err, msg = self._ValidateProduct(lic['product'])
        if err != None:
            return (err, msg)
        err, msg = self._ValidateVersion(lic['version'])
        if err != None:
            return (err, msg)
        try:
            lichash = CreateActivationRequest(lic)
            act30 = lic['activation']
            if lichash[2] not in 'X34':
                hasher = sha.new()
                hasher.update(lichash)
                hasher.update(lic['license'])
                digest = hasher.hexdigest().upper()
                lichash = lichash[:3] + textutils.SHAToBase30(digest)
                errs, lichash = ValidateAndNormalizeRequest(lichash)
            act = act30.replace('-', '')[3:]
            hexact = textutils.BaseConvert(act, textutils.BASE30, textutils.BASE16)
            while len(hexact) < 20:
                hexact = '0' + hexact

            config._locale_valid = 0
            valid = control.validate(lichash, lic['os'], lic['version'][:lic['version'].find('.')], hexact)
            valid = config._locale_valid
        except:
            valid = 0

        if not valid:
            return (kLicenseCorrupt, _('Invalid license activation'))
        daysleft = self._GetTermDaysLeft(lic)
        if not daysleft == -1:
            lic['daysleft'] = _('unlimited')
        else:
            if daysleft == -2:
                return (kLicenseCorrupt, _('Invalid date or termdays in file'))
            if daysleft == 0:
                return (kLicenseExpired, None)
            if daysleft > 12 and lic['license'][0] == 'T':
                return (kLicenseCorrupt, _('Invalid date or termdays in file'))
            if daysleft > 190 and lic['license'][0] != 'T':
                return (kLicenseCorrupt, _('Invalid date or termdays in file'))
            lic['daysleft'] = str(daysleft) + _(' days left')
        errs = hostinfo.IDMatch(lic['hostinfo'])
        if len(errs) > 0:
            return (kLicenseHostMismatch, None)
        return (kLicenseOK, [])

    def _DeactivateLicense(self):
        """Deactivate the current license, if any, by unloading it and
        removing the activation file from disk.  Returns (success,
        filename) where filename is None or the license.act file name,
        if any."""
        license = self.fLicenseData.get('license', '')
        if license.startswith('T'):
            return (False, None)
        filename = self.fLicenseData.get('filename', None)
        if filename:
            try:
                os.remove(filename)
            except:
                return (False, filename)

            self.__ReleaseLicense()
            return (True, filename)
        return (False, None)

    def __GrabLicense(self, lic, filename):
        """ Grab one user slot in the given license, if available and
        not exceeding allowed number of users """
        status, info = self._ValidateLicenseDict(lic, filename)
        if status != kLicenseOK:
            return (status, info)
        self.__ReleaseLicense()
        self.__fLicense = lic['license']
        self.fLicenseData = lic
        self.fLicenseFile = filename
        self.emit('license-ok')
        return (kLicenseOK, info)

    def __ReleaseLicense(self):
        """ Release one users slot in current in-use license """
        if self.__fLicense == None:
            return
        self.__fLicense = None
        self.fLicenseData = None
        self.fLicenseFile = None
