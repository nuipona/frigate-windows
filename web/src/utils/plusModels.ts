/** Matching Frigate+ models against native Windows detector backends. */

type PlusModel = {
  supportedDetectors: string[];
};

/** The native Windows detectors that can run a model. */
export function describeSupportedDetectors(model: PlusModel): string {
  return model.supportedDetectors.join(", ");
}
