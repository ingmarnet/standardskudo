<?php
declare(strict_types=1);

namespace Standard\Skudo\Console\Command;

use Magento\Framework\App\Config\ScopeConfigInterface;
use Standard\Skudo\Model\ChangeLogRetention;
use Symfony\Component\Console\Command\Command;
use Symfony\Component\Console\Input\InputInterface;
use Symfony\Component\Console\Input\InputOption;
use Symfony\Component\Console\Output\OutputInterface;

/**
 * `bin/magento skudo:changelog:prune [--days=N] [--dry-run]`
 *
 * M7. La cola de cambios no tenía ni comando de limpieza ni cron, y vive en
 * la base de producción del cliente. Este comando es la mitad manual (para el
 * primer recorte de una cola que ya creció, y para poder ENSAYARLO antes con
 * `--dry-run`); `Cron\PruneChangeLog` es la mitad desatendida.
 *
 * Lo que borra y lo que nunca borra está en `Model\ChangeLogRetention`. Acá
 * sólo se resuelve el margen —opción, o configuración, en ese orden— y se
 * informa el resultado con los números que permiten auditarlo: hasta dónde
 * leyó el ingestor, desde cuándo no se mueve ese número, cuántas filas tiene
 * la cola y cuántas se borraron.
 */
class PruneChangeLog extends Command
{
    public function __construct(
        private readonly ChangeLogRetention $retention,
        private readonly ScopeConfigInterface $scopeConfig,
        ?string $name = null,
    ) {
        parent::__construct($name);
    }

    protected function configure(): void
    {
        $this->setName('skudo:changelog:prune')
            ->setDescription(
                'Poda standard_skudo_change_log: borra sólo las filas que el '
                . 'ingestor de StandardSkudo ya consumió y que además son más '
                . 'viejas que el margen de seguridad.'
            )
            ->addOption(
                'days',
                null,
                InputOption::VALUE_REQUIRED,
                'Margen de seguridad en días. Por defecto, el valor de '
                . ChangeLogRetention::CONFIG_PATH_SAFETY_DAYS
            )
            ->addOption(
                'dry-run',
                null,
                InputOption::VALUE_NONE,
                'No borra: informa cuántas filas se borrarían, con el MISMO '
                . 'predicado que usaría el borrado'
            );

        parent::configure();
    }

    protected function execute(InputInterface $input, OutputInterface $output): int
    {
        $days = $input->getOption('days');
        $safetyDays = $days === null ? $this->configuredDays() : (int) $days;

        try {
            $result = $this->retention->prune($safetyDays, (bool) $input->getOption('dry-run'));
        } catch (\InvalidArgumentException $error) {
            $output->writeln('<error>' . $error->getMessage() . '</error>');
            return Command::INVALID;
        }

        $output->writeln(sprintf(
            'cola: %d fila(s); el ingestor consumió hasta change_id %d%s',
            $result['queue_rows'],
            $result['consumed_change_id'],
            $result['watermark_updated_at'] === null
                ? ' (nunca leyó)'
                : ' (última lectura: ' . $result['watermark_updated_at'] . ')'
        ));

        if ($result['consumed_change_id'] <= 0) {
            $output->writeln(
                '<comment>no se borró nada: no hay constancia de que el ingestor '
                . 'haya consumido ninguna fila. Borrar acá sería borrar cambios '
                . 'que nadie leyó.</comment>'
            );
            return Command::SUCCESS;
        }

        $output->writeln(sprintf(
            '%s %d fila(s) consumidas y con más de %d día(s)',
            $result['dry_run'] ? 'se borrarían' : 'borradas:',
            $result['dry_run'] ? $result['deletable'] : $result['deleted'],
            $result['safety_days']
        ));

        return Command::SUCCESS;
    }

    /**
     * El margen configurado, con el default del propio modelo como respaldo:
     * una instancia sin `config.xml` aplicado (o con el valor borrado a mano)
     * no debe caer a 0 días, que sería el margen más agresivo posible.
     */
    private function configuredDays(): int
    {
        $configured = $this->scopeConfig->getValue(
            ChangeLogRetention::CONFIG_PATH_SAFETY_DAYS
        );

        return $configured === null || $configured === ''
            ? ChangeLogRetention::DEFAULT_SAFETY_DAYS
            : (int) $configured;
    }
}
