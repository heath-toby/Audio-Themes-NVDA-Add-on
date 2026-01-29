# Audio Themes Add-on For NVDA (Modernized for NVDA 2025)

## Important Disclaimer

This version of the Audio Themes add-on has been modernized for NVDA 2025 compatibility by Tobias using **Claude Code** (Anthropic's AI coding assistant).

**I am not a professional programmer.** If you have programming expertise and notice issues or areas for improvement, please feel free to audit, edit, or contribute to this codebase. Pull requests and code reviews are very welcome.

## What's New in Version 8.1.0

- **Browse mode support**: Sounds now work properly in browse mode (arrow key navigation on web pages and documents). Sounds play once per object, whether arrowing or tabbing.
- **Improved audio queuing**: Sounds for container roles such as dialog, menu, list, and window are now more likely to play fully without being cut off. Note: They may still occasionally be missed depending on how quickly focus changes.
- **Smart deduplication**: Sounds only play when navigating to a different object, preventing repeated sounds when scrolling within the same element.
- **Note**: Sounds play when navigating directly to a control (arrows, Tab, quick navigation keys). Inline controls mentioned within a line of text do not trigger sounds.

## What's New in Version 8.0.1

- **Fixed duplicate sounds**: Sounds no longer play twice when focus and navigator events fire together.

## What's New in Version 8.0

- **Replaced libaudioverse with SteamAudio**: The old audio engine (libaudioverse) was unmaintained and caused issues. Now uses Valve's SteamAudio for 3D audio positioning.
- **Removed outdated Python libraries**: Removed vendored asyncio/concurrent.futures backports that are no longer needed in modern Python.
- **WAV format only**: Audio files are now WAV format instead of OGG for better compatibility.
- **Reverb controls**: Added configurable reverb settings (room size, damping, wet/dry levels, width).
- **Improved theme editor**: Save function now always exports to .atp package for sharing.
- **NVDA 2025 compatibility**: Updated for NVDA 2025.1 and later.

---

## Original Development Status

Original add-on by Musharraf Omer. If anyone wants to contribute fixes, they can send pull requests.

If anyone wants to financially sponsor the development of this add-on, they can contact Musharraf at ibnomer2011@hotmail.com

---

# Audio Themes Add-on For NVDA
This add-on creates a virtual audio display that plays sounds when focusing or navigating objects (such as buttons, links etc...) the audio will be played in a location that corresponds to the object's location in the visual display. The add-on also enables you to activate, install, remove, edit, create, and distribute audio theme packages.


## Usage
This add-on gives  you the ability to perform the following tasks: managing your installed audio themes, editing any of the installed audio themes and creating a new audio theme.


## Copyright:
Copyright (c) 2014-2019 Musharraf Omer<ibnomer2011@hotmail.com>.

Although this add-on was started as an independent project, it evolved to be an enhanced version of the 'Unspoken' add-on by Austin Hicks (camlorn38@gmail.com) and Bryan Smart (bryansmart@bryansmart.com). The majority of this add-on's development went into creating the tools to manage, edit and create audio theme packages. So a big thank you to them for creating such a wonderful add-on, and making it available for us to build on top of their work.


## A Note on Third-party audio files:
The **Default** audio theme package in this add-on uses sounds from several sources, here is a breakdown for them:
- Unspoken 3D Audio: An add-on for NVDA
- TWBlue: A free and open source twitter client
- Mushy TalkBack: An alternative talkback with better sounds.


## Licence
Licensed under the GNU General Public License. See the file **copying** for more details.
