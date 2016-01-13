#Embedded file name: C:\src\ide\bin\2.7\src\process\wingctl.pyo
""" process/wingctl.py -- Wing IDE-specific license manager

Copyright (c) 1999-2012, Archaeopteryx Software, Inc.  All rights reserved.

"""
import sys
import os
import string
import sha
import shutil
import time
import traceback
import urllib2
import logging
logger = logging.getLogger('general')
from wingutils import fileutils
from wingutils import datatype
from wingutils import textutils
from guiutils import wgtk
from guiutils import widgets
from guiutils import dialogs
from guiutils import hypertext
from guiutils import formbuilder
import config
import mainprefs
import singleton
from guimgr import messages
import abstract
import gettext
_ = gettext.translation('src_process', fallback=1).ugettext
kAccept = _('_Accept')
kDecline = _('_Decline')

class CWingLicenseManager(abstract.CLicenseManager):
    """ Specialization of the generic license manager for use in Wing IDE """

    def __init__(self, singletons):
        """ Constructor """
        abstract.CLicenseManager.__init__(self)
        self.fSingletons = singletons
        self._fExpiringLicenseCheck = False
        self.__fObtainLicenseDialog = None
        self._fPromptForSaveDialog = False

    def LicenseCheck(self, force = False):
        """ Check whether the license being used is valid, and obtain one if not.
        Only tries to obtain or show expiration dialog if force is true or Wing
        has been running for more than ten minutes."""
        try:
            valid = self.LicenseOK()
            if not valid and not config.kSteam:
                valid = self.__FindLicenseFile()
            if not force and time.time() - self._fStartTime < 10 * 60:
                return True
            if not valid:
                self._ObtainLicense()
            elif not config.kAvail101Support:
                self.__CheckExpiringLicense()
        finally:
            return True

    def __CheckForPriorAcceptance(self, eula_text, current_filename):
        """ Check if EULA was accepted.  Checks current filename if it exists
        or filename from the last version that exists """

        def check(accepted_filename):
            digest = self.__CalcEULADigest(accepted_filename, eula_text)
            if digest is None:
                return False
            try:
                file = open(accepted_filename, 'rb')
                try:
                    data = file.read()
                finally:
                    file.close()

                return bool(data == digest)
            except:
                return False

        if os.path.isfile(current_filename):
            return check(current_filename)
        parts = config.kVersion.split('.')
        filename_parts = current_filename.split('.')
        if len(parts) == 0 or len(filename_parts) == 0 or parts[-1] != filename_parts[-1]:
            return False
        try:
            micro = int(parts[-1])
        except Exception:
            return False

        micro -= 1
        while micro >= 0:
            prior_filename = '.'.join(filename_parts[:-1] + [str(micro)])
            if os.path.isfile(prior_filename):
                return check(prior_filename)
            micro -= 1

        return False

    def AcceptEULA(self, force = False):
        """ Ask user to accept the EULA if not already done in the past. If not
        accepted, the application exits. If accepted, a flag file is created 
        and user is not asked if that file already exists and is older than
        the license file.  Use force = True to always display the dialog """
        user_dir = config.kUserWingDir
        accept_file = config.kAcceptedEULAFile
        licfile = config.GetWingFilename('LICENSE.txt')
        accepted_filename = os.path.normpath(fileutils.join(user_dir, accept_file))
        if config.kPlatform == 'win32':
            accepted_filename = accepted_filename.lower()
        try:
            eula_file = open(licfile, 'r')
            try:
                eula_text = eula_file.read()
            finally:
                eula_file.close()

        except:
            wgtk.HideSplashScreen()
            msg = _('Unable to read End User License Agreement from %s. Please check your Wing installation.') % licfile

            def exit_cb():
                sys.exit(0)

            buttons = [dialogs.CButtonSpec(_('_Exit Wing'), exit_cb, wgtk.STOCK_CANCEL)]
            dlg = messages.CMessageDialog(self.fSingletons, _('Unable to read EULA'), msg, [], buttons)
            dlg.AllowSheetWindow(False)
            dlg.BlockingRunAsModal()
            return

        if not force and self.__CheckForPriorAcceptance(eula_text, accepted_filename):
            return
        accepted = {'accepted': False}

        def accept_cb():
            accepted['accepted'] = True
            digest = self.__CalcEULADigest(accepted_filename, eula_text)
            if digest is not None and os.path.isdir(user_dir):
                file = open(accepted_filename, 'wb')
                try:
                    file.write(digest)
                finally:
                    file.close()

            wgtk.UnHideSplashScreen()

        def decline_cb():
            accepted['accepted'] = False

        wgtk.HideSplashScreen()
        text_styles = {}
        dlg = CEULADialog(self.fSingletons.fWinMgr, text_styles, eula_text, accept_cb, decline_cb)
        dlg.BlockingRunAsModal()
        if not accepted['accepted']:
            sys.exit(0)

    def _ValidateProduct(self, license_product):
        """ Check license product: By default we just accept it but descendents 
        can override this. Should return (err, msg) tuple. """
        cur_product = config.kProductCode
        lic_product = config.k_WingideNameToCodeMap.get(license_product, None)
        if lic_product == None:
            return (abstract.kLicenseCorrupt, _("License product '%s' is not a valid product code.") % license_product)
        if cur_product > lic_product:
            cur_prod_name = config.k_ProductNames[cur_product]
            lic_prod_name = config.k_ProductNames[lic_product]
            return (abstract.kLicenseWrongProduct, _('License is for Wing IDE %s and cannot be used with Wing IDE %s') % (lic_prod_name, cur_prod_name))
        return (None, None)

    def _ValidateVersion(self, license_version):
        """ Check license version: By default we just accept it but descendents 
        can override this. Should return (err, msg) tuple. """
        lic_maj, lic_min = license_version.split('.')
        prd_maj, prd_min, prd_rel = config.kVersion.split('.')
        if lic_maj < prd_maj:
            return (abstract.kLicenseWrongProduct, _('License is for version %s:  An upgrade is needed to run %s') % (license_version, config.kVersion))
        else:
            return (None, None)

    def __TryLicenseFiles(self, basename):
        """Try to use the license in given license file or any additional
        activations associated with it.  Prefers valid permanent licenses
        over any found trial licenses."""
        act_file = basename
        errors = []
        dirname, filename = os.path.split(basename)
        candidates = os.listdir(dirname)
        candidates.sort()
        valid_trials = []
        for c in candidates:
            act_file = fileutils.join(dirname, c)
            if c.startswith(filename) and os.path.isfile(act_file):
                status, info = self.UseLicense(act_file)
                if status == abstract.kLicenseOK:
                    lic = abstract.ReadLicenseDict(act_file)
                    if lic['license'].startswith('T'):
                        valid_trials.append(act_file)
                    else:
                        return (True, errors)
                else:
                    act_errors = self._StatusToErrString((status, info))
                    if len(act_errors) > 0:
                        act_errors.insert(0, act_file)
                        errors.extend(act_errors)

        if len(valid_trials) > 0:
            status, info = self.UseLicense(valid_trials[-1])
            return (True, errors)
        return (False, errors)

    def __FindLicenseFile(self):
        """Find valid license file, first looking in the given user
        directory for a license file and then in the global directory.
        Returns True if a valid license file is found."""
        user_file = os.path.normpath(fileutils.join(config.kUserWingDir, 'license.act'))
        success, user_errors = self.__TryLicenseFiles(user_file)
        if success:
            return True
        global_file = os.path.normpath(config.GetWingFilename('license.act'))
        success, global_errors = self.__TryLicenseFiles(global_file)
        if success:
            return True
        if len(global_errors) > 0:
            user_errors.extend(global_errors)
        user_errors.insert(0, _('No valid license file was found.  The following errors occurred:'))
        logger.warn('\n'.join(user_errors))
        return False

    def __CheckExpiringLicense(self):
        """ Alert user once that they are using an expiring license """
        if self._fExpiringLicenseCheck:
            return
        daysleft = self._GetTermDaysLeft()
        if daysleft <= 0 or daysleft > 10 and not config.kSteam:
            return
        if config.kSteam:
            title = _('Wing IDE Trial: %i Days Left') % daysleft
        else:
            title = _('License Expiring: %i Days Left') % daysleft
        trial_clause = ''
        if not config.kSteam and self.fLicenseData is not None:
            txt = self.fLicenseData.get('license', None)
            if txt is not None and txt[0] == 'T':
                trial_clause = _('Otherwise, please note that trial licenses can be renewed at least twice for a total evaluation period of 30 days.')
        if config.kSteam:
            msg = _('Your free Wing IDE trial period will expire in %s days. If you are ready, please purchase a permanent license now.  %s\n\nThanks for using Wing IDE!') % (str(daysleft), trial_clause)
        else:
            msg = _('The license you are using will expire in %s days. If you are ready, please purchase a permanent license now.  %s\n\nThanks for using Wing IDE!') % (str(daysleft), trial_clause)
        links = textutils.ParseUrlLinks(msg)
        marks = []
        for start, end, url in links:

            def open_url(url = url):
                self.fSingletons.fWingIDEApp.ExecuteURL(url)

            marks.append((start,
             end,
             'link',
             open_url))

        if config.kSteam:

            def purchase():
                self.fSingletons.fWingIDEApp.ExecuteURL(config.kStoreURL)

            buttons = (dialogs.CButtonSpec(_('_Purchase License'), purchase, wgtk.STOCK_EXECUTE), dialogs.CButtonSpec(_('_Continue Working'), None, wgtk.STOCK_OK, default=True))
        else:

            def activate():
                self._ObtainLicense()

            buttons = (dialogs.CButtonSpec(_('_Use Permanent License'), activate, wgtk.STOCK_EXECUTE), dialogs.CButtonSpec(_('_Continue Working'), None, wgtk.STOCK_OK, default=True))
        dialog = messages.CMessageDialog(self.fSingletons, title, msg, marks, buttons)
        dialog.AllowSheetWindow(False)
        dialog.RunAsModal(self.fSingletons.fWinMgr.GetActiveWindow())
        self._fExpiringLicenseCheck = True

    def _ObtainLicense(self):
        """Prompt user to obtain a license, or quit if they don't get one"""
        if self._fPromptForSaveDialog:
            return
        if self.__fObtainLicenseDialog is not None:
            self.__fObtainLicenseDialog.Show()
            return
        if config.kSteam:
            self.__fObtainLicenseDialog = self.__SteamObtainLicense()
        else:
            self.__fObtainLicenseDialog = CObtainLicenseDialog(self.fSingletons)

        def closed(*args):
            self.__fObtainLicenseDialog = None

        self.__fObtainLicenseDialog.connect_while_alive('destroy', closed, self)
        self.__fObtainLicenseDialog.RunAsModal(self.fSingletons.fWinMgr.GetActiveWindow())

    def __SteamObtainLicense(self):
        """Show Obtain License dialog variant used under Steam.  This is only shown
        if the user has Wing but doesn't own it or is running a trial that has
        expired."""
        url = config.kStoreURL
        if config.kSteamAppID == 282230:
            title = _('Expired Trial')
            msg = _('The free trial period for Wing IDE has ended.  Please purchase a license from %s') % url
        else:
            title = _('No Valid License')
            msg = _('Wing could not reach Steam to verify that you own a license.  Please check your internet connection and try starting again.  If you do not own a license, please purchase one from %s') % url

        @wgtk.call_at_idle
        def exit():
            self.fSingletons.fGuiMgr._Quit(check_save=False, no_dialogs=True)

        @wgtk.call_at_idle
        def show_url(url = url):
            self.fSingletons.fWingIDEApp.ExecuteURL(url)
            exit()

        buttons = [dialogs.CButtonSpec(_('_Show Store'), show_url, wgtk.STOCK_OK, default=True), dialogs.CButtonSpec(_('Just _Quit'), exit, wgtk.STOCK_OK)]
        dlg = messages.CMessageDialog(self.fSingletons, title, msg, [], buttons, close_cb=exit)
        dlg.fWindow.fGtkWindow.setMinimumHeight(100)
        return dlg

    def __CalcEULADigest(self, accepted_filename, eula_text):
        """ Calculate the digest to put in the EULA acceptance file. """
        hasher = sha.new()
        hasher.update(accepted_filename)
        hasher.update(eula_text)
        if self.fLicenseData is not None:
            for field in ('customerid', 'name', 'company', 'product', 'version'):
                hasher.update(self.fLicenseData[field])

        digest = '\x00\x00%s\x00\x00' % hasher.digest()
        return digest

    def __CreateLicenseDir(self, lic_dir):
        """Create the license dir if needed.  Returns 1 if successful, 0 if not"""
        if os.path.isdir(lic_dir):
            return 1
        try:
            os.makedirs(lic_dir)
            return 1
        except:
            return 0

    def _CopyInLicenseDict(self, lic, pending = False):
        """ Copy given license dict over to given user file, saving any old
        copies there by adding 1, 2, 3, ... to the file name is necessary.
        Set pending=True to write a manual activation file, otherwise
        writes the actual license file. This call assumes the license has
        already been validated (if not, it will fail later when used). On
        failure, returns a non-empty list of error strings."""
        if pending:
            filename = 'license.pending'
        else:
            filename = 'license.act'
        user_file = os.path.normpath(fileutils.join(config.kUserWingDir, filename))
        act_name = user_file
        i = 1
        while os.path.exists(act_name):
            existing_lic = abstract.ReadLicenseDict(act_name)
            if existing_lic is None:
                match = False
            else:
                match = True
                for key in lic:
                    if key != 'customerdata' and existing_lic.get(key) != lic[key]:
                        match = False
                        break

            if match:
                break
            act_name = user_file + str(i)
            i += 1

        user_file = act_name
        if not self.__CreateLicenseDir(config.kUserWingDir):
            errs = [_('Unable to create the required directory %s because the disk is read-only or a file of the same name already exists.') % config.kUserWingDir]
            return errs
        if pending:
            ignore = ('activation',)
        else:
            ignore = ()
        errs = abstract.WriteLicenseDict(user_file, lic, ignore)
        if len(errs) > 0:
            return errs
        if not pending:
            self.UseLicense(user_file)
        return []


class CMultiLineLabel(wgtk.TextView):
    """ A label with better line wrapping behavior.  Qt only for now, 
    though I recall doing someng similar w/ gtk """

    def __init__(self, text = '', char_width = -1, char_height = -1, max_char_width = -1, max_char_height = -1):
        super(CMultiLineLabel, self).__init__()
        self.setText(text)
        self.SetCharSizeRequest(char_width, char_height, max_width=max_char_width, max_height=max_char_height)
        self.setReadOnly(True)
        self.document().setDocumentMargin(0)
        self.setFrameShape(wgtk.QFrame.NoFrame)
        self.setStyleSheet('background: rgba(0,0,0,0%)')
        self.setHorizontalScrollBarPolicy(wgtk.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(wgtk.Qt.ScrollBarAlwaysOff)

    def set_text(self, text):
        self.setText(text)


class CObtainLicenseDialog(dialogs.CGenericDialog):
    """Dialog used to obtain a new license"""
    kCharWidth = 60

    def __init__(self, singletons, lic = None):
        self.fSingletons = singletons
        self.fLicMgr = singletons.fLicMgr
        self.fLicense = lic
        if lic is not None:
            self.fRequest = abstract.CreateActivationRequest(lic)
        else:
            self.fRequest = None
        self.__fOptionalEntries = [None, None, None]
        self.__fInitialLicenseOK = self.fLicMgr.LicenseOK()
        self.__fProgress = None
        self.__fActivationIteration = 0
        buttons = [dialogs.CButtonSpec(_('Con_tinue'), self.__CB_Continue, wgtk.STOCK_OK, default=True), dialogs.CButtonSpec(_('_Cancel'), self.__CB_Cancel, wgtk.STOCK_CANCEL)]
        if not self.fLicMgr.LicenseOK():
            title = _('No License Found')
        else:
            title = _('Purchase License')
        dialogs.CGenericDialog.__init__(self, singletons.fWinMgr, 'no-license-dialog', title, size=None, button_spec=buttons, close_cb=self.__CB_Cancel)
        self.AllowSheetWindow(False)

    def _CreateMainPanel(self):
        vbox = wgtk.VBox()
        vbox.set_border_width(10)
        vbox.set_spacing(10)
        self.__fTitle = CMultiLineLabel(char_width=self.kCharWidth)
        vbox.pack_start(self.__fTitle, expand=False, fill=False)
        self.__fPageOne = self._CreatePageOne()
        self.__fPageTwo = self._CreatePageTwo()
        mpanel = wgtk.VBox()
        mpanel.add(self.__fPageOne)
        self.__fMainPanel = mpanel
        vbox.pack_start(mpanel, expand=True, fill=True)
        wgtk.InitialShowAll(vbox)
        self.__UpdateGUI()
        return vbox

    def _CreatePageOne(self):
        vbox = wgtk.VBox()
        vbox.set_spacing(5)
        radio_group = wgtk.RadioButtonGroup()
        radios = []

        def radio(txt, tip, w):
            return self.__CreateRadio(txt, tip, w, radio_group, radios, vbox)

        canned_lic = self.__ReadVendorFile()
        if not canned_lic:
            tip = _('Trial licenses can be obtained directly from Wing IDE and last for 10 days.  Once the license expires, you can renew it twice for a total of 30 days.  You will need to email us at support@wingware.com if you need more time than that.')
            tmpstr = _('Obtain or extend a trial license')
            self.__fGetTrialArea, self.__fRadioGetTrial, ignore = radio(tmpstr, tip, None)
        else:
            self.__fGetTrialArea = None
            self.__fRadioGetTrial = None
        if not canned_lic:
            tip = _('Licenses can be purchased from our website and via fax or email.  Even if you do not want to pay online, filling out an order helps to expedite the process and provides a printable form for off-line processing.')
            chbox, self.__fRadioPurchase, ignore = radio(_('Purchase a permanent license'), tip, None)
        else:
            self.__fRadioPurchase = None
        self.__fLicenseIDEntry = wgtk.Entry()
        user_file = os.path.normpath(fileutils.join(config.kUserWingDir, 'license.act'))
        if os.path.exists(user_file):
            lic = abstract.ReadLicenseDict(user_file)
            if lic is not None:
                txt = lic.get('license', '')
                if len(txt) > 0 and txt[0] != 'T':
                    self.__fLicenseIDEntry.set_text(txt)
        tip = _('Wing requires that you activate each license before using it.  If you run out of activations and you are a licensed user, please contact us at sales@wingware.com.  We use activations to prevent abuse, but never to limit the activities of valid users. ')
        if canned_lic:
            title = _('Activate your pre-loaded license:')
        else:
            title = _('Install and activate a permanent license.\nEnter license id:')
        chbox, self.__fRadioActivate, ignore = radio(title, tip, self.__fLicenseIDEntry)
        if canned_lic:
            self.__fLicenseIDEntry.set_text(canned_lic)
            self.__fLicenseIDEntry.set_editable(False)
        tip = _('You can try Wing for a short period of time without any license.  At the end of the allocated time, this dialog will reappear and you will be given the option to obtain a trial or permanent license or quit Wing after saving any unsaved changes.')
        self.__fRunArea, self.__fRadioRun, ignore = radio(_('10 minute emergency session'), tip, None)
        tip = _('You have already used up the allowed time period during which Wing will run without any license.  If you do not want to get a trial or permanent license, you must quit now after saving any unsaved changes.')
        self.__fExitArea, self.__fRadioExit, ignore = radio(_('Exit after saving any unsaved changes'), tip, None)
        if canned_lic:
            self.__fRadioActivate.set_active(True)
        elif self.__fInitialLicenseOK:
            self.__fRadioPurchase.set_active(True)
        else:
            self.__fRadioGetTrial.set_active(True)
        wgtk.timeout_add_while_alive(5000, self.__UpdateRunExitChoices, self)
        wgtk.InitialShowAll(vbox)
        for radio in radios:
            wgtk.gui_connect(radio, 'toggled', self.__UpdateGUI)

        return vbox

    def _CreatePageTwo(self):
        canned_lic = self.__ReadVendorFile()
        vbox = wgtk.VBox()
        vbox.set_spacing(5)
        radio_group = wgtk.RadioButtonGroup()
        radios = []

        def radio(txt, tip, w):
            return self.__CreateRadio(txt, tip, w, radio_group, radios, vbox)

        title = _('Choose your preferred method of license activation:')
        label = CMultiLineLabel(title, char_width=self.kCharWidth)
        self.__fSecondaryTitle = label
        vbox.pack_start(label, expand=False, fill=True)
        tip = _('This is the easiest way to activate your new license.  Wing sends only your license id and your request code to our server.  We do not collect information about you or your machine here.')
        proxy_config = wgtk.IconButton(_('Configure Proxy...'), wgtk.STOCK_NETWORK)

        def config_proxy(*args):
            self.fSingletons.fWingIDEApp._ConfigureProxy()

        wgtk.gui_connect(proxy_config, 'clicked', config_proxy)
        align = wgtk.HBox()
        align.pack_start(proxy_config, expand=0, fill=0)
        chbox, self.__fRadioDirect, ignore = radio(_('Recommended: Connect Wing IDE directly to wingware.com'), tip, align)
        self.__fProxyConfigArea = align
        self.__fManualEntry = wgtk.Entry()
        wgtk.gui_connect(self.__fManualEntry, 'changed', self.__UpdateGUI)
        tip = _('If your machine does not have a network connection that can access port 80 (http) on wingware.com, you will need to activate your license manually by entering your license id and request code on a machine that does have network access.  If you cannot browse the web at all, email the information to us at support@wingware.com and we will email you the activation code.')
        chbox, self.__fRadioManual, self.__fManualLabel = radio('', tip, self.__fManualEntry)
        self.__fRadioDirect.set_active(True)
        if not canned_lic:
            text = _('You may enter these optional values to record in your activation file for your own tracking purposes.  This data is not sent to our server:')
            label = CMultiLineLabel(text, char_width=self.kCharWidth)
            vbox.pack_start(label, expand=False, fill=True)
            table = wgtk.Table(3, 2)
            self.__fOptionalValueAreas = [label, table]
            for i, item in enumerate((_('Licensed user'), _('Company/Org'), _('Notes'))):
                label = wgtk.Label(item)
                label.set_line_wrap(True)
                table.attach(label, 0, 1, i, i + 1)
                entry = wgtk.Entry()
                wgtk.gui_connect(entry, 'changed', self.__UpdateCustData)
                self.__fOptionalEntries[i] = (label, entry)
                table.attach(entry, 1, 2, i, i + 1)

            vbox.pack_start(table, expand=False, fill=True)
        else:
            self.__fOptionalEntries = []
            self.__fOptionalValueAreas = []
        wgtk.InitialShowAll(vbox)
        wgtk.SetVisible(self.__fProxyConfigArea, False)
        for radio in radios:
            wgtk.gui_connect(radio, 'toggled', self.__UpdateGUI)

        return vbox

    def __SetTitleMarkup(self, markup):
        self.__fTitle.setText(markup)

    def __CreateRadio(self, txt, tip, w, radio_group, radios, vbox):
        radio = wgtk.RadioButton()
        radios.append(radio)
        radio.set_radio_group(radio_group)
        wgtk.set_tooltip(radio, tip)
        chbox = wgtk.HBox()
        chbox.set_spacing(10)
        align = wgtk.Alignment(0.0, 0.0, 1.0, 0.0)
        align.add(radio)
        tooltip1 = wgtk.TooltipBox(tip, child=align)
        chbox.pack_start(tooltip1, expand=False, fill=True)
        cvbox = wgtk.VBox()
        label = CMultiLineLabel(txt, char_width=self.kCharWidth)
        cvbox.pack_start(label, expand=False, fill=True)
        if w is not None:
            cvbox.pack_start(w, expand=False, fill=True)
        chbox.pack_start(cvbox, expand=True, fill=True)
        vbox.pack_start(chbox, expand=False, fill=True)
        return (chbox, radio, label)

    def __UpdateGUI(self, *args):
        if self.__fMainPanel.get_children()[0] == self.__fPageOne:
            self.__UpdatePageOneGUI()
        else:
            self.__UpdatePageTwoGUI()

    def __UpdatePageOneGUI(self):
        wgtk.SetVisible(self.__fTitle)
        if self.fLicMgr.LicenseOK():
            self.__SetTitleMarkup('<b>' + _('To change to another license, you may now:') + '</b>')
            title = _('Change License')
        else:
            self.__SetTitleMarkup('<b>' + _('Wing is running without a valid license. You may now:') + '</b>')
            title = _('No Valid License')
        self.fWindow.SetTitle(title)
        if self.__fGetTrialArea is not None:
            wgtk.SetVisible(self.__fGetTrialArea, visible=not self.__fInitialLicenseOK)
        self.__UpdateRunExitChoices()

    def __UpdateRunExitChoices(self, *args):
        try:
            if self.__fInitialLicenseOK:
                wgtk.SetVisible(self.__fExitArea, False)
                wgtk.SetVisible(self.__fRunArea, False)
            elif time.time() - self.fLicMgr._fStartTime < 10 * 60:
                wgtk.SetVisible(self.__fExitArea, False)
                wgtk.SetVisible(self.__fRunArea)
            else:
                wgtk.SetVisible(self.__fExitArea)
                wgtk.SetVisible(self.__fRunArea, False)
            self.__fLicenseIDEntry.set_sensitive(self.__fRadioActivate.get_active())
        finally:
            return True

    def __UpdatePageTwoGUI(self):
        if self.fLicense is not None and self.fLicense['license'].startswith('T'):
            self.__SetTitleMarkup('<b>' + _('Your trial license has been created.  To activate it you can:') + '</b>')
            self.fWindow.SetTitle(_('Activate Trial License'))
        else:
            self.__SetTitleMarkup('<b>' + _('Please choose your preferred method of license activation:') + '</b>')
            self.fWindow.SetTitle(_('Activate License'))
        self.__fManualLabel.set_text(_('Or:  Activate manually at http://wingware.com/activate.  You will need your license id %s and request code %s.  Then enter the provided activation key here:') % (self.fLicense['license'], self.fRequest))
        self.fLicMgr._CopyInLicenseDict(self.fLicense, pending=True)
        if self.fLicense is None:
            wgtk.SetVisible(self.__fTitle)
            wgtk.SetVisible(self.__fSecondaryTitle, False)
            for area in self.__fOptionalValueAreas:
                wgtk.SetVisible(area, False)

        else:
            wgtk.SetVisible(self.__fTitle, False)
            opt_sens = len(self.__fManualEntry.get_text()) == 0
            opt_show = not self.fLicense['license'].startswith('T')
            wgtk.SetVisible(self.__fSecondaryTitle, visible=opt_show)
            for i, area in enumerate(self.__fOptionalValueAreas):
                wgtk.SetVisible(area, visible=opt_show)
                if opt_show:
                    area.set_sensitive(opt_sens)

        self.__fManualEntry.set_sensitive(self.__fRadioManual.get_active())

    def __CB_Continue(self):
        if self.__fMainPanel.get_children()[0] == self.__fPageOne:
            return self.__PageOneContinue()
        else:
            return self.__PageTwoContinue()

    def __PageOneContinue(self):
        if self.__fRadioGetTrial is not None and self.__fRadioGetTrial.get_active():
            self.fLicense = abstract.CreateTrialLicenseDict()
            self.__UpdateCustData()
            self.__ShowPage(2)
            return True
        if self.__fRadioPurchase is not None and self.__fRadioPurchase.get_active():
            title = _('Opening Browser')
            msg = _('Click OK to open http://wingware.com/store in a web browser.  Once you have purchased a license, return here to activate it.')
            marks = []
            links = textutils.ParseUrlLinks(msg)
            for start, end, url in links:

                def open_url(url = url):
                    self.fSingletons.fWingIDEApp.ExecuteURL(url)

                marks.append((start,
                 end,
                 'link',
                 open_url))

            def doit(app = self.fSingletons.fWingIDEApp):
                app.ExecuteURL('http://wingware.com/store')

            buttons = [dialogs.CButtonSpec(_('_OK'), doit, wgtk.STOCK_OK, default=True), dialogs.CButtonSpec(_('_Cancel'), None, wgtk.STOCK_CANCEL)]
            dlg = messages.CMessageDialog(self.fSingletons, title, msg, marks, buttons)
            dlg.RunAsModal(self)
            return True
        if self.__fRadioActivate.get_active():
            id = self.__fLicenseIDEntry.get_text()
            errs, lic = abstract.ValidateAndNormalizeLicenseID(id)
            if len(errs) == 0 and id[0] == 'T':
                errs.append(_('You cannot enter a trial license id here'))
            if len(errs) > 0:
                msg = _('Invalid license id: %s. Please check and correct it.  Errors found were:\n\n%s') % (id, '\n'.join(errs))
                buttons = [dialogs.CButtonSpec(_('_OK'), None, wgtk.STOCK_OK)]
                dlg = messages.CMessageDialog(self.fSingletons, _('Invalid License ID'), msg, [], buttons)
                dlg.RunAsModal(self)
                return True
            self.fLicense = abstract.CreateLicenseDict(lic)
            self.__UpdateCustData()
            if id[2] in 'LWM':
                self.__StartActivation()
            else:
                self.__ShowPage(2)
            return True
        if self.__fRadioRun.get_active():
            pass
        elif self.__fRadioExit.get_active():

            def done():

                def doit():
                    singleton.GetSingletons().fGuiMgr._Quit(check_save=False, no_dialogs=True)

                wgtk.idle_add(doit)

            self.fLicMgr._fPromptForSaveDialog = True

            def cancel(licmgr = self.fLicMgr):
                licmgr._fPromptForSaveDialog = False

            self.fSingletons.fGuiMgr.fSaveMgr.PromptForSave(action_cb=done, parent_win=self, initial_prompt=1, cancel_cb=cancel)
        else:
            self.fLicMgr.LicenseCheck()

    def __PageTwoContinue(self):
        if self.__fRadioDirect.get_active():
            self.__StartActivation()
            return True
        if self.__fRadioManual.get_active():
            act = self.__fManualEntry.get_text()
            errs, act = abstract.ValidateAndNormalizeActivation(act)
            if len(errs) > 0:
                title = _('Invalid License ID')
                msg = _('Invalid activation key: %s. Please check and correct it.  Errors found were:\n\n%s') % (self.__fManualEntry.get_text(), '\n'.join(errs))
                self.__ErrorDlg(title, msg)
                return True
            actbase = os.path.normpath(fileutils.join(config.kUserWingDir, 'license.pending'))
            i = 1
            act_file = actbase
            errs = []
            while os.path.exists(act_file) and i <= 10:
                try:
                    lic2 = abstract.ReadLicenseDict(act_file)
                except:
                    lic2 = None

                if lic2 is not None and lic2.get('license') == self.fLicense['license']:
                    lic2['activation'] = act
                    err, info = self.fLicMgr._ValidateLicenseDict(lic2, None)
                    if err == abstract.kLicenseOK:
                        self.fLicense = lic2
                        try:
                            os.unlink(act_file)
                        except:
                            pass

                        break
                    errs.append('Pending activation ' + str(act_file) + ' -- failed:')
                    errs.extend([ '  ' + t for t in self.fLicMgr._StatusToErrString((err, info)) ])
                    errs.append('\n')
                act_file = actbase + str(i)
                i += 1

            self.fLicense['activation'] = act
            err, info = self.fLicMgr._ValidateLicenseDict(self.fLicense, None)
            if err != abstract.kLicenseOK:
                msg = _('Invalid activation key: %s. Please check and correct it.') % self.__fManualEntry.get_text()
                errs.append('Current activation -- failed:')
                errs.extend([ '  ' + t for t in self.fLicMgr._StatusToErrString((err, info)) ])
                if len(errs) > 0:
                    msg += _('  Validation errors were:\n\n%s') % '\n'.join(errs)
                title = _('Invalid License ID')
                self.__ErrorDlg(title, msg)
                return True
            if not self.__CopyInLicense():
                return True
        else:
            self.fLicMgr.LicenseCheck()

    def __CB_Cancel(self):
        if self.__fMainPanel.get_children()[0] == self.__fPageOne:
            self.fLicMgr.LicenseCheck()
        else:
            self.__ShowPage(1)
            return True

    def __ShowPage(self, num):
        self.__fMainPanel.remove(self.__fMainPanel.get_children()[0])
        if num == 1:
            self.__fMainPanel.add(self.__fPageOne)
        else:
            self.__fMainPanel.add(self.__fPageTwo)
        self.__UpdateGUI()

    def __ErrorDlg(self, title, msg, extra_buttons = [], sheet = True):
        buttons = list(extra_buttons)
        buttons.append(dialogs.CButtonSpec(_('_OK'), None, wgtk.STOCK_OK))
        dlg = messages.CMessageDialog(self.fSingletons, title, msg, [], buttons)
        dlg.AllowSheetWindow(sheet)
        dlg.RunAsModal(self)

    def __UpdateCustData(self, *args):
        if self.fLicense is None:
            return
        custdata = []
        for label, area in self.__fOptionalEntries:
            area = area.get_text().strip()
            if len(area) > 0:
                custdata.append(area)

        custdata = ', '.join(custdata)
        self.fLicense['customerdata'] = custdata
        self.fRequest = abstract.CreateActivationRequest(self.fLicense)

    def __StartActivation(self):
        """Start the activation process, which is done asyncronously via a
        generator."""

        def closed():
            self.__fProgress = None
            self.__fActivationIteration = -1

        self.__fProgress = dialogs.CProgressDialog(self.fSingletons.fWinMgr, _('Activating'), _('Submitting activation request. Please wait'), closed)
        self.__fProgress.RunAsModal(self)
        wgtk.idle_add_while_alive(self.__CB_ActivationStep, self)
        self.__fActivationIteration = 0
        lic = self.fLicense['license']
        if lic[2] not in 'LWM':
            self.__fActivationGenerator = self.__ActivationIteration()
            self.__fProgress.fProgressBar.set_text(_('Connecting to wingware.com'))
            self.__fProgress.fProgressBar.set_fraction(0.2)
        else:
            self.__fActivationGenerator = self.__BulkActivationIteration()
            self.__fProgress.fProgressBar.set_text(_('Preparing activation'))
            self.__fProgress.fProgressBar.set_fraction(0.2)

    def __CB_ActivationStep(self):
        """Called by idle for each step in submitting an activation request"""
        try:
            self.__fActivationIteration = self.__fActivationGenerator.next()
        except StopIteration:
            self.__fActivationIteration = -1
        except:
            self.__fActivationIteration = -1
            self.__ShowUnableToConnectDialog()
            from wingutils import reflect
            reflect.ReportCurrentException(suppress_gui=True)

        if self.__fActivationIteration == -1:
            if not self.destroyed():
                if self.__fProgress is not None:
                    self.__fProgress.Close()
                    self.__fProgress = None
                self.__fActivationGenerator = None
            return False
        else:
            return True

    def __ShowUnableToConnectDialog(self):
        wgtk.SetVisible(self.__fProxyConfigArea)
        self.fSingletons.fWingIDEApp._ShowUnableToConnectDialog(self)

    def __ActivationIteration(self):
        """Do one iteration in the activation process"""
        while self.__fActivationIteration != -1:
            if self.__fActivationIteration == 0:
                lic = self.fLicense['license']
                url = 'http://wingware.com/activate&license=%s&request=%s&noheader=1' % (lic, self.fRequest)
                svc = urllib2.urlopen(url)
                yield 1
            elif self.__fActivationIteration == 1:
                self.__fProgress.fProgressBar.set_text(_('Waiting for Reply'))
                self.__fProgress.fProgressBar.set_fraction(0.4)
                txt = svc.read()
                yield 2
            elif self.__fActivationIteration == 2:
                self.__fProgress.fProgressBar.set_text(_('Reading Reply'))
                self.__fProgress.fProgressBar.set_fraction(0.6)
                svc.close()
                err, act, exceeded, need_upgrade = self.__GetActivationErrors(txt, lic)
                if err is not None:
                    title = _('Could not obtain activation key')
                    extra_buttons = []
                    if exceeded:

                        def show_license_page():
                            url = 'http://wingware.com/license&license=%s' % lic
                            self.fSingletons.fWingIDEApp.ExecuteURL(url)

                        extra_buttons.append(dialogs.CButtonSpec(_('Add Activations'), show_license_page, wgtk.STOCK_ADD, pack_start=True))
                    if need_upgrade:

                        def show_upgrade_page():
                            url = 'https://wingware.com/store/upgrade&license-id=%s' % lic
                            self.fSingletons.fWingIDEApp.ExecuteURL(url)

                        extra_buttons.append(dialogs.CButtonSpec(_('Upgrade'), show_upgrade_page, wgtk.STOCK_GO_UP, pack_start=True))
                    self.__ErrorDlg(title, err, extra_buttons)
                    yield -1
                else:
                    yield 3
            elif self.__fActivationIteration == 3:
                self.__fProgress.fProgressBar.set_text(_('Validating Activation Key'))
                self.__fProgress.fProgressBar.set_fraction(0.8)
                self.fLicense['activation'] = act
                err, info = self.fLicMgr._ValidateLicenseDict(self.fLicense, None)
                if err != abstract.kLicenseOK:
                    msg = _('Internal activation error: %s. Please report this to support@wingware.com.') % self.__fManualEntry.get_text()
                    errs = self.fLicMgr._StatusToErrString((err, info))
                    if len(errs) > 0:
                        msg += _('  Validation errors were:\n\n%s') % '\n'.join(errs)
                    title = _('Invalid License ID')
                    self.__ErrorDlg(title, msg)
                    yield -1
                else:
                    yield 4
            elif self.__fActivationIteration == 4:
                self.__fProgress.fProgressBar.set_text(_('Writing Activation File'))
                self.__fProgress.fProgressBar.set_fraction(1.0)
                if self.__CopyInLicense():
                    self.__fProgress.Close()
                    self.Close()
                yield -1

    def __BulkActivationIteration(self):
        """Do one iteration in the activation process"""
        while self.__fActivationIteration != -1:
            if self.__fActivationIteration == 0:
                if abstract.bulkctl is None:
                    title = _('Invalid license')
                    err = _('The license code you provided is invalid for this product')
                    self.__ErrorDlg(title, err)
                    yield -1
                yield 1
            elif self.__fActivationIteration == 1:
                self.__fProgress.fProgressBar.set_text(_('Validating License'))
                self.__fProgress.fProgressBar.set_fraction(0.2)
                if not abstract.bulkctl.check(self.fLicense['license']):
                    title = _('Invalid license')
                    err = _('The license code you provided is invalid -- please check it and try again')
                    self.__ErrorDlg(title, err)
                    yield -1
                if not self.fLicMgr._ValidatePlatform(self.fLicense['license'], abstract.control.get_os()):
                    title = _('Invalid license')
                    err = _('The license code you provided not valid for this operating system')
                    self.__ErrorDlg(title, err)
                    yield -1
                yield 2
            elif self.__fActivationIteration == 2:
                self.__fProgress.fProgressBar.set_text(_('Creating Activation'))
                self.__fProgress.fProgressBar.set_fraction(0.4)
                req = self.fRequest
                act = abstract.bulkctl._hash16(req)
                act30 = textutils.BaseConvert(act, textutils.BASE16, textutils.BASE30)
                while len(act30) < 17:
                    act30 = '1' + act30

                act30 = 'AXX' + act30
                act = abstract.AddHyphens(act30)
                yield 3
            elif self.__fActivationIteration == 3:
                self.__fProgress.fProgressBar.set_text(_('Validating Activation'))
                self.__fProgress.fProgressBar.set_fraction(0.6)
                self.fLicense['activation'] = act
                err, info = self.fLicMgr._ValidateLicenseDict(self.fLicense, None)
                if err != abstract.kLicenseOK:
                    msg = _('Internal activation error: %s. Please report this to support@wingware.com.') % self.__fManualEntry.get_text()
                    errs = self.fLicMgr._StatusToErrString((err, info))
                    if len(errs) > 0:
                        msg += _('  Validation errors were:\n\n%s') % '\n'.join(errs)
                    title = _('Invalid License ID')
                    self.__ErrorDlg(title, msg)
                    yield -1
                else:
                    yield 4
            elif self.__fActivationIteration == 4:
                self.__fProgress.fProgressBar.set_text(_('Writing Activation File'))
                self.__fProgress.fProgressBar.set_fraction(1.0)
                if self.__CopyInLicense():
                    self.__fProgress.Close()
                    self.Close()
                yield -1

    def __GetActivationErrors(self, txt, lic):
        """Get activation errors, if any, given a server response"""
        exceeded = False
        need_upgrade = False
        if txt.find('INVALID:') >= 0:
            txt = txt[txt.find('INVALID:'):]
            if txt.find('\n') >= 0:
                txt = txt[:txt.find('\n')]
        elif txt.find('AXX') >= 0:
            txt = txt[txt.find('AXX'):]
            txt = txt[:23]
        if txt.startswith('INVALID'):
            try:
                codes = txt.split('\n')[0].split(':')[1]
            except:
                codes = 'X'

            errs = []
            for c in codes:
                if c == 'M':
                    errs.append(_('Internal error (missing field)'))
                elif c == 'L':
                    errs.append(_('Internal error (invalid license)'))
                elif c == 'R':
                    errs.append(_('Internal error (invalid request code)'))
                elif c == 'E':
                    if lic[0] == 'T':
                        errs.append(_('You have exceeded the allowed number of trial licenses on this machine. Please email your license id %s to support@wingware.com if you need more time.\n\nSorry about the inconvenience, and thanks for trying Wing IDE!') % lic)
                    else:
                        errs.append(_('You have exceeded your allowed number of license activations. You can increase activation limits at http://wingware.com/license or by emailing your license id %s to support@wingware.com.\n\nSorry for the inconvenience and thanks for using Wing IDE!') % lic)
                        exceeded = True
                elif c == 'O':
                    errs.append(_('That license id %s cannot be activated on additional operating systems.  Please contact sales@wingware.com if you wish to upgrade your license.') % lic)
                    need_upgrade = True
                elif c == 'U':
                    errs.append(_('We have no record of that license id %s.  Please check it and try again.') % lic)
                elif c == 'K':
                    errs.append('That license id %s has been revoked, possibly because a replacement has been issued or due to abuse.  Please check it and try again.' % lic)
                elif c == 'X':
                    errs.append(_('Internal error (unexpected server return value).  This may indicate network problems such as a misconfigured proxy server.'))
                elif c == 'V':
                    errs.append(_('That license id %s can only be activated on specially patched versions of Wing.  Please contact your vendor  or sales@wingware.com.') % lic)
                elif c == 'H':
                    errs.append(_('This license is not valid for this version of Wing.  Please upgrade at https://wingware.com/store/upgrade&license-id=%s  Upgrades to new major releases made within a year of your original purchase are free.') % lic)
                    need_upgrade = True

            return ('\n'.join(errs),
             None,
             exceeded,
             need_upgrade)
        errs, act = abstract.ValidateAndNormalizeActivation(txt)
        if len(errs) > 0:
            return (_('Received an invalid activation key "%s" from the server.  This may indicate network problems such as a misconfigured proxy server.  Errors found were:\n\n%s') % (txt[:23], '\n'.join(errs)),
             None,
             exceeded,
             need_upgrade)
        return (None,
         act.strip(),
         exceeded,
         need_upgrade)

    def __CopyInLicense(self):
        try:
            errs = self.fLicMgr._CopyInLicenseDict(self.fLicense)
            if len(errs) > 0:
                title = _('Failed to use activated license')
                msg = _('The following errors occurred while copying the license activation file into place:\n\n')
                msg += '\n'.join(errs)
                self.__ErrorDlg(title, msg)
                return False
        except:
            title = _('Failed to use activated license')
            msg = _('The following exception occurred while copying the license activation file into place:\n\n')
            msg += traceback.format_exc()
            self.__ErrorDlg(title, msg)
            return False

        self.fLicMgr.LicenseCheck()
        if self.fLicMgr.LicenseOK():
            self.fLicMgr._fExpiringLicenseCheck = True
            if self.fLicense['license'].startswith('T'):
                title = _('Trial License Generated')
                msg = _('Your 10-day trial license has been created and activated.  If this is your first trial, you will be able to extend the license term twice for up to 30 days.\n\nThanks for trying Wing IDE!')
                self.__ErrorDlg(title, msg, sheet=False)
                return True
            else:
                title = _('License Activated')
                msg = _('License activation succeeded.  Thanks for using Wing IDE!')
                self.__ErrorDlg(title, msg, sheet=False)
                return True
        else:
            title = _('Unexpected License Activation Error')
            msg = _('A validated license was corrupted during copying.  Please send email to support@wingware.com')
            self.__ErrorDlg(title, msg)
            return False

    def __ReadVendorFile(self):
        try:
            vendor_file = fileutils.join(config.kWingHome, 'resources', 'vendor')
            vendor_data = abstract.ReadLicenseDict(vendor_file)
            if vendor_data and vendor_data.has_key('license'):
                errs, lic = abstract.ValidateAndNormalizeLicenseID(vendor_data['license'])
                if errs:
                    print 'Invalid preload license line:', repr(vendor_data['license'])
                else:
                    return lic
        except:
            from wingutils import reflect
            reflect.ReportCurrentException(suppress_gui=True)
            return None


class CEULADialog(dialogs.CGenericDialog):
    """EULA acceptance dialog"""

    def __init__(self, win_mgr, text_styles, eula_text, accept_cb, decline_cb):
        """Constructor:
        
        win_mgr      -- window manager to use
        
        """
        self.__fEULAText = eula_text
        self.__fTextStyles = text_styles
        button_spec = [dialogs.CButtonSpec(kAccept, accept_cb, wgtk.STOCK_YES, default=True), dialogs.CButtonSpec(kDecline, decline_cb, wgtk.STOCK_NO)]
        dialogs.CGenericDialog.__init__(self, win_mgr, 'eula-dialog', _('License Agreement'), None, button_spec)

    def _CreateMainPanel(self):
        font1 = [('weight', 'bold')]
        header = wgtk.Label(_('Wing IDE is licensed software.  Its use is subject to the terms and conditions of the following license agreement:'))
        header.set_alignment(0.0, 0.5)
        header.set_padding(10, 10)
        header.set_line_wrap(True)
        footer = wgtk.Label(_("If you accept the terms of this license agreement, press 'Accept'.  Otherwise, press 'Decline.'"))
        footer.set_alignment(0.0, 0.5)
        footer.set_padding(10, 10)
        footer.set_line_wrap(True)
        text = unicode(self.__fEULAText, 'iso-8859-1').encode('utf-8')
        para_list = text.split('\n\n')
        for i, para in enumerate(para_list):
            lines = para.split('\n')
            para = ' '.join([ l.strip() for l in lines ])
            para_list[i] = para

        text = '\n\n'.join(para_list)
        htext = hypertext.CHyperText(self.__fTextStyles, char_size_request=(80, 10, 10), visible=True)
        htext.SetText(text)
        swin = wgtk.ScrolledWindow(hscrollbar_policy=wgtk.POLICY_NEVER, child=htext, request_child_size=True, visible=True)
        frame = wgtk.Frame()
        frame.add(swin)
        frame.set_border_width(2)
        frame.set_shadow_type(wgtk.SHADOW_IN)
        vbox = wgtk.VBox()
        vbox.pack_start(header, expand=False)
        vbox.pack_start(frame, expand=True)
        vbox.pack_start(footer, expand=False)
        wgtk.InitialShowAll(vbox)
        return vbox
