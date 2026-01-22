# coding: utf-8

# Copyright (c) 2014-2019 Musharraf Omer
# This file is covered by the GNU General Public License.

import wx
import config
import gui
from gui import guiHelper
from gui.settingsDialogs import SettingsPanel
from .handler import AudioThemesHandler, audiotheme_changed


import addonHandler

addonHandler.initTranslation()


class AudioThemesSettingsPanel(SettingsPanel):
    # Translators: Title for the settings panel in NVDA's multi-category settings
    title = _("Audio Themes")

    def makeSettings(self, settingsSizer):
        sHelper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)

        # Translators: label for the checkbox to enable or disable audio themes
        self.enableThemesCheckbox = sHelper.addItem(
            wx.CheckBox(self, label=_("Enable audio themes"))
        )

        # Translators: label for a combobox containing a list of installed audio themes
        self.installedThemesChoice = sHelper.addLabeledControl(
            _("Select theme:"), wx.Choice, choices=[]
        )

        # Theme action buttons
        bHelper = sHelper.addItem(guiHelper.ButtonHelper(wx.HORIZONTAL))
        # Translators: label for a button to show info about an audio theme
        self.aboutThemeButton = bHelper.addButton(self, label=_("&About"))
        # Translators: label for a button to remove an audio theme
        self.removeThemeButton = bHelper.addButton(self, label=_("&Remove"))
        # Translators: label for a button to add a new audio theme
        self.addThemeButton = bHelper.addButton(self, label=_("Add &New..."))

        # Translators: label for a checkbox to toggle the 3D mode
        self.play3dCheckbox = sHelper.addItem(
            wx.CheckBox(self, label=_("Play sounds in 3D mode"))
        )

        # Translators: label for a checkbox to toggle the speaking of object role
        self.speakRoleCheckbox = sHelper.addItem(
            wx.CheckBox(self, label=_("Speak roles such as button, edit box, link etc."))
        )

        # Translators: label for a checkbox to toggle the use of audio themes during say all
        self.useInSayAllCheckbox = sHelper.addItem(
            wx.CheckBox(self, label=_("Speak roles during say all"))
        )

        # Translators: label for a checkbox to toggle whether the volume of this add-on should follow the synthesizer volume
        self.useSynthVolumeCheckbox = sHelper.addItem(
            wx.CheckBox(self, label=_("Use speech synthesizer volume"))
        )

        # Translators: label for a slider to set the volume of this add-on
        self.volumeSlider = sHelper.addLabeledControl(
            _("Audio themes volume:"),
            wx.Slider,
            minValue=0,
            maxValue=100
        )

        # Translators: label for a checkbox to toggle reverb effect
        self.useReverbCheckbox = sHelper.addItem(
            wx.CheckBox(self, label=_("Use reverb effect"))
        )

        # Translators: label for room size slider
        self.roomSizeSlider = sHelper.addLabeledControl(
            _("Room size (0-100):"),
            wx.Slider,
            minValue=0,
            maxValue=100
        )

        # Translators: label for damping slider
        self.dampingSlider = sHelper.addLabeledControl(
            _("Damping (0-100):"),
            wx.Slider,
            minValue=0,
            maxValue=100
        )

        # Translators: label for wet level slider
        self.wetLevelSlider = sHelper.addLabeledControl(
            _("Wet level (0-100):"),
            wx.Slider,
            minValue=0,
            maxValue=100
        )

        # Translators: label for dry level slider
        self.dryLevelSlider = sHelper.addLabeledControl(
            _("Dry level (0-100):"),
            wx.Slider,
            minValue=0,
            maxValue=100
        )

        # Translators: label for width slider
        self.widthSlider = sHelper.addLabeledControl(
            _("Width (0-100):"),
            wx.Slider,
            minValue=0,
            maxValue=100
        )

        # Bind events
        self.aboutThemeButton.Bind(wx.EVT_BUTTON, self.onAbout)
        self.removeThemeButton.Bind(wx.EVT_BUTTON, self.onRemove)
        self.addThemeButton.Bind(wx.EVT_BUTTON, self.onAdd)
        self.enableThemesCheckbox.Bind(wx.EVT_CHECKBOX, self.onEnableChanged)
        self.useSynthVolumeCheckbox.Bind(wx.EVT_CHECKBOX, self.onSynthVolumeChanged)
        self.installedThemesChoice.Bind(wx.EVT_CHOICE, self.onThemeSelectionChanged)

        self._initialize_at_state()
        self._maintain_state()

    def onEnableChanged(self, event):
        enabled = self.enableThemesCheckbox.IsChecked()
        self._updateControlsState(enabled)

    def onSynthVolumeChanged(self, event):
        self.volumeSlider.Enable(not self.useSynthVolumeCheckbox.IsChecked())

    def _updateControlsState(self, enabled):
        """Enable/disable controls based on whether audio themes are enabled."""
        for ctrl in (
            self.installedThemesChoice,
            self.play3dCheckbox,
            self.speakRoleCheckbox,
            self.useInSayAllCheckbox,
            self.useSynthVolumeCheckbox,
            self.volumeSlider,
            self.useReverbCheckbox,
            self.roomSizeSlider,
            self.dampingSlider,
            self.wetLevelSlider,
            self.dryLevelSlider,
            self.widthSlider,
            self.aboutThemeButton,
            self.removeThemeButton,
            self.addThemeButton,
        ):
            ctrl.Enable(enabled)
        if enabled:
            self.volumeSlider.Enable(not self.useSynthVolumeCheckbox.IsChecked())
            self.onThemeSelectionChanged(None)

    @property
    def selected_theme(self):
        selection = self.installedThemesChoice.GetSelection()
        if selection != wx.NOT_FOUND:
            return self.installedThemesChoice.GetClientData(selection)

    def _initialize_at_state(self):
        conf = config.conf["audiothemes"]
        self.enableThemesCheckbox.SetValue(conf["enable_audio_themes"])
        self.play3dCheckbox.SetValue(conf["audio3d"])
        self.speakRoleCheckbox.SetValue(conf["speak_roles"])
        self.useInSayAllCheckbox.SetValue(conf["use_in_say_all"])
        self.useSynthVolumeCheckbox.SetValue(conf["use_synth_volume"])
        self.volumeSlider.SetValue(conf["volume"])
        self.useReverbCheckbox.SetValue(conf.get("use_reverb", True))
        self.roomSizeSlider.SetValue(conf.get("RoomSize", 10))
        self.dampingSlider.SetValue(conf.get("Damping", 100))
        self.wetLevelSlider.SetValue(conf.get("WetLevel", 9))
        self.dryLevelSlider.SetValue(conf.get("DryLevel", 30))
        self.widthSlider.SetValue(conf.get("Width", 100))

    def _maintain_state(self):
        self.audio_themes = sorted(AudioThemesHandler.get_installed_themes())
        self.installedThemesChoice.Clear()
        for theme in self.audio_themes:
            self.installedThemesChoice.Append(theme.name, theme)
        # Select the active theme
        for i, theme in enumerate(self.audio_themes):
            if theme.folder == config.conf["audiothemes"]["active_theme"]:
                self.installedThemesChoice.SetSelection(i)
                break
        self._updateControlsState(self.enableThemesCheckbox.IsChecked())

    def onSave(self):
        conf = config.conf["audiothemes"]
        conf["enable_audio_themes"] = self.enableThemesCheckbox.IsChecked()
        if self.selected_theme:
            conf["active_theme"] = self.selected_theme.folder
        conf["audio3d"] = self.play3dCheckbox.IsChecked()
        conf["speak_roles"] = self.speakRoleCheckbox.IsChecked()
        conf["use_in_say_all"] = self.useInSayAllCheckbox.IsChecked()
        conf["use_synth_volume"] = self.useSynthVolumeCheckbox.IsChecked()
        conf["volume"] = self.volumeSlider.GetValue()
        conf["use_reverb"] = self.useReverbCheckbox.IsChecked()
        conf["RoomSize"] = self.roomSizeSlider.GetValue()
        conf["Damping"] = self.dampingSlider.GetValue()
        conf["WetLevel"] = self.wetLevelSlider.GetValue()
        conf["DryLevel"] = self.dryLevelSlider.GetValue()
        conf["Width"] = self.widthSlider.GetValue()

    def postSave(self):
        audiotheme_changed.notify()

    def onAbout(self, event):
        if not self.selected_theme:
            return
        wx.MessageBox(
            # Translators: content of a message box containing theme information
            _("Name: {name}\nAuthor: {author}\n\n{summary}").format(
                **self.selected_theme.todict()
            ),
            # Translators: title for a message containing theme information
            _("About Audio Theme"),
            style=wx.ICON_INFORMATION,
        )

    def onRemove(self, event):
        theme = self.selected_theme
        if not theme:
            return
        confirm = wx.MessageBox(
            # Translators: message asking the user to confirm the removal of an audio theme
            _(
                "This can not be undone.\nAre you sure you want to remove audio theme {name}?"
            ).format(name=theme.name),
            # Translators: title of a message asking the user to confirm the removal of an audio theme
            _("Remove Audio Theme"),
            style=wx.YES_NO | wx.ICON_WARNING,
        )
        if confirm == wx.YES:
            AudioThemesHandler.remove_audio_theme(theme)
            self._maintain_state()

    def onAdd(self, event):
        openFileDlg = wx.FileDialog(
            self,
            # Translators: the title of a file dialog to browse to an audio theme package
            message=_("Choose an audio theme package"),
            # Translators: theme file type description
            wildcard=_("Audio Theme Packages") + " (*.atp)|*.atp",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if openFileDlg.ShowModal() == wx.ID_OK:
            filename = openFileDlg.GetPath().strip()
            openFileDlg.Destroy()
            if filename:
                AudioThemesHandler.install_audio_themePackage(filename)
                self._maintain_state()

    def onThemeSelectionChanged(self, event):
        flag = self.selected_theme is not None
        self.aboutThemeButton.Enable(flag)
        self.removeThemeButton.Enable(flag)
