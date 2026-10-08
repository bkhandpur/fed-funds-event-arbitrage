import tseslint from "typescript-eslint";
import hooks from "eslint-plugin-react-hooks";
import a11y from "eslint-plugin-jsx-a11y";
export default tseslint.config(
 {ignores:[".next/**",".deps/**",".venv/**","next-env.d.ts","playwright-report/**","test-results/**","node_modules/**"]},
 ...tseslint.configs.recommended,
 {...a11y.flatConfigs.recommended, files:["**/*.tsx"], plugins:{...a11y.flatConfigs.recommended.plugins,"react-hooks":hooks},rules:{...a11y.flatConfigs.recommended.rules,"react-hooks/rules-of-hooks":"error","react-hooks/exhaustive-deps":"warn"}},
);
