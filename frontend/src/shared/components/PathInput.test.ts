import { describe, expect, it } from "vitest";
import { dropMessage } from "./PathInput";

describe("перетаскивание в поле одного файла", () => {
  it("молчит, если взят единственный подходящий файл", () => {
    expect(dropMessage(["C:\\a\\t.pptx"], ["C:\\a\\t.pptx"], ["pptx"])).toBeNull();
  });

  it("говорит, какие файлы пропущены и почему", () => {
    expect(dropMessage(["C:\\a\\t.xlsx"], [], ["pptx", ".potx"])).toBe("Пропущены (нужен файл .pptx, .potx): t.xlsx");
  });

  it("говорит, что из нескольких подходящих взят первый", () => {
    const paths = ["/x/a.csv", "/x/b.csv", "/x/c.txt"];
    expect(dropMessage(paths, ["/x/a.csv", "/x/b.csv"], ["csv"])).toBe(
      "Пропущены (нужен файл .csv): c.txt. Поле принимает один файл: взят a.csv",
    );
  });
});
