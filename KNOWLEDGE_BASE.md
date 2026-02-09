# Knowledge base

## Pump Enable as Switch
Unfortunately DAB Pumps backend server exposes the Pump Enable/Disable as a multi-select option with values 'Enable', 'Disable' and '---'.
After choosing 'Enable' or 'Disable', the value will fallback to the default '---' after a few seconds. That makes it hard to determine whether the pump is enabled or disabled.

A simple workaround is to define your own template entity that exposes this as a switch.

In `configuration.yaml` add:
```
template: !include templates.yaml
```
Then in `templates.yaml` add:
```
- switch:
    name: "Esybox Pump enable"
    unique_id: "esybox_pumpenable"
    availability: >-
      {{ states('sensor.esybox_pumpstatus') != 'unavailable' }}
    state: >-
      {% if states('select.esybox_pumpdisable') == 'Enable' %}
        {{ True }}
      {% elif states('select.esybox_pumpdisable') == 'Disable' %}
        {{ False }}
      {% else %}
          {{ states('sensor.esybox_pumpstatus') != 'Manual disabled' }}
      {% endif %}
    turn_on:
      action: select.select_option
      data:
        option: "Enable"
      target: 
        entity_id: select.esybox_pumpdisable
    turn_off:
      action: select.select_option
      data:
        option: "Disable"
      target: 
        entity_id: select.esybox_pumpdisable
```

## Powershower start/stop as Switch
Similar to Pump Enable/Disable, the Powershower command is presented by the DAB Pumps backend server as a multi-select option with values 'Start', 'Stop' and '---'.
After choosing 'Start' or 'Stop', the value will fallback to the default '---' after a few seconds. That makes it hard to determine whether powershower is currently active.

A simple workaround is to define your own template entity that exposes this as a switch.

In `configuration.yaml` add:
```
template: !include templates.yaml
```
Then in `templates.yaml` add:
```
- switch:
    name: "Esybox Power Shower enable"
    unique_id: "esybox_powershowerenable"
    availability: >-
      {{ states('sensor.esybox_powershowercountdown') != 'unavailable' }}
    state: >-
      {% if states('select.esybox_powershowercommand') == 'Start' %}
        {{ True }}
      {% elif states('select.esybox_powershowercommand') == 'Stop' %}
        {{ False }}
      {% else %}
        {{ has_value('sensor.esybox_powershowercountdown')  }}
      {% endif %}
    turn_on:
      action: select.select_option
      data:
        option: "Start"
      target: 
        entity_id: select.esybox_powershowercommand
    turn_off:
      action: select.select_option
      data:
        option: "Stop"
      target: 
        entity_id: select.esybox_powershowercommand
```

