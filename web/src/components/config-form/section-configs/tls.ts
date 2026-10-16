import type { SectionConfigOverrides } from "./types";

const tls: SectionConfigOverrides = {
  base: {
    sectionDocs: "/configuration/tls",
    restartRequired: ["enabled", "cert_file", "key_file"],
    fieldOrder: ["enabled", "cert_file", "key_file"],
    advancedFields: ["cert_file", "key_file"],
    uiSchema: {
      cert_file: {
        "ui:options": { size: "md" },
      },
      key_file: {
        "ui:options": { size: "md" },
      },
    },
  },
};

export default tls;
