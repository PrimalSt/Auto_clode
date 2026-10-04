import { Button, Modal, Table, Text } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconPlus } from "@tabler/icons-react";
import { useNavigate } from "react-router";
import { ErrorAlert } from "../../shared/components/ErrorAlert";
import { Page } from "../../shared/components/Page";
import { dateTime } from "../../shared/format";
import { useThemes } from "./queries";
import { ThemeImport } from "./ThemeImport";

export default function ThemesPage() {
  const themes = useThemes();
  const navigate = useNavigate();
  const [opened, modal] = useDisclosure(false);
  return (
    <Page
      title="Оформление"
      subtitle="Шаблоны презентаций: макеты, роли, слайды-образцы"
      actions={
        <Button leftSection={<IconPlus size={16} />} onClick={modal.open}>
          Загрузить шаблон
        </Button>
      }
    >
      <ErrorAlert error={themes.error} />
      {themes.data?.length === 0 && <Text c="dimmed">Шаблонов пока нет. Загрузите корпоративный шаблон .pptx.</Text>}
      {!!themes.data?.length && (
        <Table highlightOnHover verticalSpacing="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Название</Table.Th>
              <Table.Th>id</Table.Th>
              <Table.Th>Версия</Table.Th>
              <Table.Th>Файл</Table.Th>
              <Table.Th>Слайдов-образцов</Table.Th>
              <Table.Th>Изменён</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {themes.data.map((t) => (
              <Table.Tr key={t.id} style={{ cursor: "pointer" }} onClick={() => navigate(`/themes/${t.id}`)}>
                <Table.Td fw={500}>{t.name}</Table.Td>
                <Table.Td>
                  <code>{t.id}</code>
                </Table.Td>
                <Table.Td>{t.version}</Table.Td>
                <Table.Td>{t.current.original_name}</Table.Td>
                <Table.Td>{t.current.manifest.slides.length}</Table.Td>
                <Table.Td>{dateTime(t.current.imported_at)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      <Modal opened={opened} onClose={modal.close} title="Загрузить шаблон" size="lg">
        <ThemeImport onDone={(out) => !out.skipped && navigate(`/themes/${out.record.id}`)} />
      </Modal>
    </Page>
  );
}
