import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      maxWidth: { page: "1280px" },
    },
  },
};

export default config;
