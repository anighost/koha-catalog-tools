#!/usr/bin/perl
# create_label_batch.pl
# Run via koha-shell to create a Koha label batch and export it as a PDF.
# Mirrors label-create-pdf.pl exactly, without CGI/auth overhead.
#
# Usage (inside koha-shell):
#   perl /path/to/create_label_batch.pl BARCODE1,BARCODE2,... TEMPLATE_ID LAYOUT_ID OUTPUT_PDF_PATH
#
# Writes the label PDF to OUTPUT_PDF_PATH and prints one line to stdout:
#   batch_id:items_added
#
# Dies with an error message on failure.

use Modern::Perl;
use C4::Context;
use C4::Creators;
use C4::Labels;

my ($barcode_str, $template_id, $layout_id, $output_path) = @ARGV;
die "Usage: $0 barcodes template_id layout_id output_pdf_path\n"
    unless $barcode_str && $output_path;

$template_id //= 1;   # Avery 5160 | 1 x 2-5/8
$layout_id   //= 17;  # Dishari Label

my @barcodes = split /,/, $barcode_str;
my $dbh = C4::Context->dbh;

# ── Assign next batch_id (max + 1) ────────────────────────────────────────
my ($max_batch) = $dbh->selectrow_array(
    "SELECT COALESCE(MAX(batch_id), 0) FROM creator_batches WHERE creator = 'Labels'"
);
my $batch_id = ($max_batch // 0) + 1;

# ── Find a valid borrower number (needed for the FK) ──────────────────────
my ($borrower_number) = $dbh->selectrow_array(
    "SELECT borrowernumber FROM borrowers ORDER BY borrowernumber ASC LIMIT 1"
);
$borrower_number //= 1;

# ── Insert one row per barcode into creator_batches ───────────────────────
my $added = 0;
for my $bc (@barcodes) {
    $bc =~ s/^\s+|\s+$//g;
    next unless $bc;
    my ($itemnumber) = $dbh->selectrow_array(
        "SELECT itemnumber FROM items WHERE barcode = ?", undef, $bc
    );
    unless ($itemnumber) {
        warn "Barcode '$bc' not found in items — skipped\n";
        next;
    }
    $dbh->do(
        "INSERT INTO creator_batches
            (batch_id, item_number, borrower_number, branch_code, creator)
         VALUES (?, ?, ?, 'DFL', 'Labels')",
        undef, $batch_id, $itemnumber, $borrower_number
    );
    $added++;
}

die "No items were added to batch (barcodes not found in Koha?)\n" unless $added;

# ── Generate PDF — mirrors label-create-pdf.pl exactly ────────────────────
# Redirect STDOUT to the output file so C4::Creators::PDF writes there.
open(STDOUT, '>:raw', $output_path) or die "Cannot write to $output_path: $!\n";

our $pdf      = C4::Creators::PDF->new(InitVars => 0);
my  $batch    = C4::Labels::Batch->retrieve(batch_id => $batch_id);
our $template = C4::Labels::Template->retrieve(template_id => $template_id, profile_id => 1);
my  $layout   = C4::Labels::Layout->retrieve(layout_id => $layout_id);

sub _calc_next_label_pos {
    my ($row_count, $col_count, $llx, $lly) = @_;
    if ($col_count < $template->get_attr('cols')) {
        $llx = ($llx + $template->get_attr('label_width') + $template->get_attr('col_gap'));
        $col_count++;
    } else {
        $llx = $template->get_attr('left_margin');
        if ($row_count == $template->get_attr('rows')) {
            $pdf->Page();
            $lly = (  $template->get_attr('page_height')
                    - $template->get_attr('top_margin')
                    - $template->get_attr('label_height') );
            $row_count = 1;
        } else {
            $lly = ($lly - $template->get_attr('row_gap') - $template->get_attr('label_height'));
            $row_count++;
        }
        $col_count = 1;
    }
    return ($row_count, $col_count, $llx, $lly);
}

sub _print_text {
    my $label_text = shift;
    foreach my $text_line (@$label_text) {
        $pdf->Font($text_line->{'font'});
        $pdf->FontSize($text_line->{'font_size'});
        $pdf->Text($text_line->{'text_llx'}, $text_line->{'text_lly'}, $text_line->{'line'});
    }
}

$| = 1;

$pdf->Compress(1);
$pdf->Mbox(0, 0, $template->get_attr('page_width'), $template->get_attr('page_height'));

my ($row_count, $col_count, $llx, $lly) = $template->get_label_position(1);

my $items = $batch->get_attr('items');

LABEL_ITEMS: foreach my $item (@{$items}) {
    if ($layout->get_attr('printing_type') eq 'ALT') {
        my $label_a = C4::Labels::Label->new(
            batch_id         => $batch_id,
            item_number      => $item->{'item_number'},
            llx              => $llx,
            lly              => $lly,
            width            => $template->get_attr('label_width'),
            height           => $template->get_attr('label_height'),
            top_text_margin  => $template->get_attr('top_text_margin'),
            left_text_margin => $template->get_attr('left_text_margin'),
            barcode_type     => $layout->get_attr('barcode_type'),
            printing_type    => 'BIB',
            guidebox         => $layout->get_attr('guidebox'),
            oblique_title    => $layout->get_attr('oblique_title'),
            font             => $layout->get_attr('font'),
            font_size        => $layout->get_attr('font_size'),
            scale_width      => $layout->get_attr('scale_width'),
            scale_height     => $layout->get_attr('scale_height'),
            callnum_split    => $layout->get_attr('callnum_split'),
            justify          => $layout->get_attr('text_justify'),
            format_string    => $layout->get_attr('format_string'),
            text_wrap_cols   => $layout->get_text_wrap_cols(
                label_width      => $template->get_attr('label_width'),
                left_text_margin => $template->get_attr('left_text_margin'),
            ),
        );
        $pdf->Add($label_a->draw_guide_box) if $layout->get_attr('guidebox');
        _print_text($label_a->create_label());
        ($row_count, $col_count, $llx, $lly) = _calc_next_label_pos($row_count, $col_count, $llx, $lly);

        my $label_b = C4::Labels::Label->new(
            batch_id         => $batch_id,
            item_number      => $item->{'item_number'},
            llx              => $llx,
            lly              => $lly,
            width            => $template->get_attr('label_width'),
            height           => $template->get_attr('label_height'),
            top_text_margin  => $template->get_attr('top_text_margin'),
            left_text_margin => $template->get_attr('left_text_margin'),
            barcode_type     => $layout->get_attr('barcode_type'),
            printing_type    => 'BAR',
            guidebox         => $layout->get_attr('guidebox'),
            oblique_title    => $layout->get_attr('oblique_title'),
            font             => $layout->get_attr('font'),
            font_size        => $layout->get_attr('font_size'),
            scale_width      => $layout->get_attr('scale_width'),
            scale_height     => $layout->get_attr('scale_height'),
            callnum_split    => $layout->get_attr('callnum_split'),
            justify          => $layout->get_attr('text_justify'),
            format_string    => $layout->get_attr('format_string'),
            text_wrap_cols   => $layout->get_text_wrap_cols(
                label_width      => $template->get_attr('label_width'),
                left_text_margin => $template->get_attr('left_text_margin'),
            ),
        );
        $pdf->Add($label_b->draw_guide_box) if $layout->get_attr('guidebox');
        _print_text($label_b->create_label());
        ($row_count, $col_count, $llx, $lly) = _calc_next_label_pos($row_count, $col_count, $llx, $lly);
        next LABEL_ITEMS;
    }

    my $label = C4::Labels::Label->new(
        batch_id         => $batch_id,
        item_number      => $item->{'item_number'},
        llx              => $llx,
        lly              => $lly,
        width            => $template->get_attr('label_width'),
        height           => $template->get_attr('label_height'),
        top_text_margin  => $template->get_attr('top_text_margin'),
        left_text_margin => $template->get_attr('left_text_margin'),
        barcode_type     => $layout->get_attr('barcode_type'),
        printing_type    => $layout->get_attr('printing_type'),
        guidebox         => $layout->get_attr('guidebox'),
        oblique_title    => $layout->get_attr('oblique_title'),
        font             => $layout->get_attr('font'),
        font_size        => $layout->get_attr('font_size'),
        scale_width      => $layout->get_attr('scale_width'),
        scale_height     => $layout->get_attr('scale_height'),
        callnum_split    => $layout->get_attr('callnum_split'),
        justify          => $layout->get_attr('text_justify'),
        format_string    => $layout->get_attr('format_string'),
        text_wrap_cols   => $layout->get_text_wrap_cols(
            label_width      => $template->get_attr('label_width'),
            left_text_margin => $template->get_attr('left_text_margin'),
        ),
    );
    $pdf->Add($label->draw_guide_box) if $layout->get_attr('guidebox');
    my $label_text = $label->create_label();
    _print_text($label_text) if $label_text;
    ($row_count, $col_count, $llx, $lly) = _calc_next_label_pos($row_count, $col_count, $llx, $lly);
}

$pdf->End();
close(STDOUT);

# Print result to STDERR so the Flask app can read it (STDOUT is the PDF file now)
print STDERR "$batch_id:$added\n";
