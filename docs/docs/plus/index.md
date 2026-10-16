---
id: index
title: Models
---

<a href="https://frigate.video/plus" target="_blank" rel="nofollow">Frigate+</a> provides object-detection models trained for security-camera images. Account-specific training fine-tunes a base model with verified images from your cameras.

A subscription includes model trainings and access to base models published while the subscription is active. See the [integration guide](../integrations/plus.md) to connect the native Windows application.

## Available model types

Frigate+ may offer several architectures. The model picker shows only files compatible with the native Windows detector selected for that model.

| Model type | Windows use |
| --- | --- |
| `mobiledet` | TFLite on the CPU detector |
| `yolonas` | ONNX Runtime or OpenVINO, depending on the published variant |
| `yolov9` | ONNX Runtime or OpenVINO, depending on the published variant |

Test a base model before spending a training request. Resolution and architecture affect both accuracy and inference time. Object-filter scores often need retuning after changing architecture.

## Supported detector types

The native Windows application accepts Frigate+ variants for:

| Hardware | Detector | Typical model |
| --- | --- | --- |
| CPU TFLite | `cpu` | `mobiledet` |
| Intel CPU/GPU/NPU through OpenVINO | `openvino` | `yolonas` or `yolov9` |
| CPU, DirectML, or CUDA through ONNX Runtime | `onnx` | `yolonas` or `yolov9` |

Models that contain only unsupported detector variants are hidden by the API. If a model supports several detectors, its metadata is reduced to the Windows-compatible variants.

## Improving your model

Some users may find that Frigate+ models result in more false positives initially, but by submitting true and false positives, the model will improve. With all the new images now being submitted by subscribers, future base models will improve as more and more examples are incorporated. Note that only images with at least one verified label will be used when training your model. Submitting an image from Frigate as a true or false positive will not verify the image. You still must verify the image in Frigate+ in order for it to be used in training.

- **Submit both true positives and false positives**. This will help the model differentiate between what is and isn't correct. You should aim for a target of 80% true positive submissions and 20% false positives across all of your images. If you are experiencing false positives in a specific area, submitting true positives for any object type near that area in similar lighting conditions will help teach the model what that area looks like when no objects are present.
- **Lower your thresholds a little in order to generate more false/true positives near the threshold value**. For example, if you have some false positives that are scoring at 68% and some true positives scoring at 72%, you can try lowering your threshold to 65% and submitting both true and false positives within that range. This will help the model learn and widen the gap between true and false positive scores.
- **Submit diverse images**. For the best results, you should provide at least 100 verified images per camera. Keep in mind that varying conditions should be included. You will want images from cloudy days, sunny days, dawn, dusk, and night. As circumstances change, you may need to submit new examples to address new types of false positives. For example, the change from summer days to snowy winter days or other changes such as a new grill or patio furniture may require additional examples and training.

## Available label types

Frigate+ models support a more relevant set of objects for security cameras. The labels for annotation in Frigate+ are configurable by editing the camera in the Cameras section of Frigate+. Currently, the following objects are supported:

- **People**: `person`, `face`, `baby`
- **Vehicles**: `car`, `motorcycle`, `bicycle`, `boat`, `school_bus`, `garbage truck`, `license_plate`
- **Delivery Logos**: `amazon`, `usps`, `ups`, `fedex`, `dhl`, `an_post`, `purolator`, `postnl`, `nzpost`, `postnord`, `gls`, `dpd`, `canada_post`, `royal_mail`
- **Animals**: `dog`, `cat`, `deer`, `horse`, `bird`, `raccoon`, `fox`, `bear`, `cow`, `squirrel`, `goat`, `rabbit`, `skunk`, `kangaroo`, `possum`, `rodent`
- **Other**: `package`, `waste_bin`, `bbq_grill`, `robot_lawnmower`, `umbrella`, `baby_stroller`

Other object types available in the default Frigate model are not available. Additional object types will be added in future releases.

### Candidate labels

Candidate labels are also available for annotation. These labels don't have enough data to be included in the model yet, but using them will help add support sooner. You can enable these labels by editing the camera settings.

Where possible, these labels are mapped to existing labels during training. For example, any `duck` labels are mapped to `bird` until support for new labels is added.

- **Vehicles**: `tractor`, `golf_cart`, `bus`, `airplane`, `helicopter`, `rickshaw`, `scooter`
- **Delivery Logos**: `bpost`, `auspost`, `aramex`, `transoflex`, `parcelforce`, `hermes`, `cargus`, `fan_courier`, `sameday`, `la_poste`
- **Animals**: `badger`, `chicken`, `duck`, `turkey`, `groundhog`, `boar`, `hedgehog`, `wombat`, `bobcat`, `mustelid`, `mountain_lion`, `crocodile`, `monkey`, `coyote`, `porcupine`, `sheep`, `snake`, `lizard`, `heron`, `elk`, `moose`, `pig`, `donkey`, `civet`
- **Other**: `sports_ball`, `drone`, `lawnmower`

Candidate labels are not available for automatic suggestions.

### Label attributes

Frigate has special handling for some labels when using Frigate+ models. `face`, `license_plate`, and delivery logos such as `amazon`, `ups`, and `fedex` are considered attribute labels which are not tracked like regular objects and do not generate review items directly. In addition, the `threshold` filter will have no effect on these labels. You should adjust the `min_score` and other filter values as needed.

In order to have Frigate start using these attribute labels, you will need to add them to the list of objects to track:

```yaml
objects:
  track:
    - person
    - face
    - license_plate
    - dog
    - cat
    - car
    - amazon
    - fedex
    - ups
    - package
```

When using Frigate+ models, Frigate will choose the snapshot of a person object that has the largest visible face. For cars, the snapshot with the largest visible license plate will be selected. This aids in secondary processing such as facial and license plate recognition for person and car objects.

![Face Attribute](/img/plus/attribute-example-face.jpg)

Delivery logos such as `amazon`, `ups`, and `fedex` labels are used to automatically assign a sub label to car objects.

![Fedex Attribute](/img/plus/attribute-example-fedex.jpg)
