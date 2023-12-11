# DAB Pumps

[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]][license]

[![hacs][hacsbadge]][hacs]
![Project Maintenance][maintenance-shield]


## Overview

Custom component to retrieve sensor information from DAB Pumps devices via the DConnect website.

## Features

1. Setup via `custom_flow` using multiple steps to authenticate with host, customize devices and options
2. Combine KM2 specific sensors into devices as suggested by [hoppel118](https://github.com/dm82m/hass-Deltasol-KM2/issues/24)
3. Group sensors into 'useful' device sensors and less important diagnostics sensors
4. Each sensor entity_id incorporate unique device name, yet presents friendly sensor name
5. Sensors that are disabled are skipped for updates, same with sensors where the state value has not changed
6. Extensive debug logging (hopefully helpful to anyone going through the same learning curve as myself)

![controller_detail](documentation/controller_detail.jpg)

## Installation

1. Click install.
2. See the [github repository](https://github.com/ankohanse/hass-dab-pumps/blob/main/README.md) for detailed setup instructions.



[releases-shield]: https://img.shields.io/github/release/ankohanse/hass-dab-pumps.svg?style=for-the-badge
[releases]: https://github.com/ankohanse/hass-dab-pumps/releases

[commits-shield]: https://img.shields.io/github/commit-activity/y/ankohanse/hass-dab-pumps.png?style=for-the-badge
[commits]: https://github.com/ankohanse/hass-dab-pumps/commits/main

[license-shield]: https://img.shields.io/github/license/ankohanse/hass-dab-pumps.svg?style=for-the-badge
[license]: https://github.com/ankohanse/hass-dab-pumps/blob/main/LICENSE

[hacs]: https://github.com/custom-components/hacs
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge

[maintenance-shield]: https://img.shields.io/badge/maintainer-Anko%20Hanse-blue.svg?style=for-the-badge

[buymecoffee]: https://www.buymeacoffee.com/ankohanse
[buymecoffeebadge]: https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow.svg?style=for-the-badge
