// eslint-config-next ships a flat-config array in v16, not a factory function.
import next from "eslint-config-next";

const config = [
  { ignores: [".next/**", "node_modules/**", "out/**"] },
  ...next,
];

export default config;
