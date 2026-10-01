import { MoonIcon, SunIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { setTheme, useTheme } from "@/lib/theme";

/** Switches between the light and dark theme; the choice is remembered in this browser. */
export function ThemeToggle() {
  const theme = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  return (
    <Tooltip>
      <TooltipTrigger
        render={<Button variant="ghost" size="icon-sm" aria-label={`Switch to the ${next} theme`} onClick={() => setTheme(next)} />}
      >
        {theme === "dark" ? <SunIcon /> : <MoonIcon />}
      </TooltipTrigger>
      <TooltipContent side="bottom">{theme === "dark" ? "Light theme" : "Dark theme"}</TooltipContent>
    </Tooltip>
  );
}
